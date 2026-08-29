"""Retention watcher: the autonomous trigger.

Cloud Scheduler publishes to cutpoint-retention-scan on a fixed interval; that
subscription pushes here. Each scan re-runs cliff detection over live ClickHouse
data, compares the result against the last fingerprint this trailer produced,
and publishes to cutpoint-analyze ONLY when a genuinely new cliff has appeared.
Nothing downstream needs a human: the API's push handler picks the message up
and runs the full diagnosis pipeline.

The fingerprint is what separates this from a cron job. Without it every tick
would re-diagnose the same cliffs forever and re-spend on Gemini each time.

Deliberately talks to ClickHouse through ingest.clickhouse_client rather than
mcp-clickhouse. The read-only MCP boundary exists for the AGENT, whose queries
are LLM-influenced. The watcher is infrastructure running a fixed query, so it
uses the ordinary client and stays outside that boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from agent.cutpoint_agent import obs, store
from api.auth import verify_google_identity

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHANGEPOINTS_SQL = REPO_ROOT / "sql" / "analysis" / "changepoints.sql"

ANALYZE_TOPIC = os.environ.get("CUTPOINT_ANALYZE_TOPIC", "cutpoint-analyze")

app = FastAPI(title="CutPoint Retention Watcher")


def _valid_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "_-" for c in value)


def active_trailers(client) -> list[str]:
    rows = client.query("SELECT DISTINCT trailer_id FROM cutpoint.trailers").result_rows
    # changepoints.sql interpolates trailer_id into query text, so anything that
    # is not a plain id never reaches the database.
    return [r[0] for r in rows if _valid_id(r[0])]


def detect_cliffs(client, trailer_id: str) -> list[dict]:
    if not _valid_id(trailer_id):
        raise ValueError(f"invalid trailer_id: {trailer_id!r}")
    sql = CHANGEPOINTS_SQL.read_text().replace("{trailer_id}", trailer_id)
    result = client.query(sql)
    return [
        {"second": int(r[0]), "drop_pct": float(r[1]), "z_score": float(r[2])}
        for r in result.result_rows
    ]


def fingerprint(cliffs: list[dict]) -> str:
    """Stable digest of the cliff set. drop_pct is rounded so ordinary jitter in
    the underlying counts does not read as a new cliff and retrigger analysis.
    """
    canonical = sorted((c["second"], round(c["drop_pct"], 3)) for c in cliffs)
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def publish_analysis(trailer_id: str, job_id: str) -> None:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(os.environ["GOOGLE_CLOUD_PROJECT"], ANALYZE_TOPIC)
    publisher.publish(
        topic, json.dumps({"trailer_id": trailer_id, "job_id": job_id}).encode()
    ).result(timeout=30)


def scan(client, publish=publish_analysis) -> list[dict]:
    """One scan pass. Returns what changed, so the handler can log it and the
    tests can assert on it without touching Pub/Sub.
    """
    triggered = []
    for trailer_id in active_trailers(client):
        cliffs = detect_cliffs(client, trailer_id)
        current = fingerprint(cliffs)
        if current == store.get_fingerprint(trailer_id):
            continue
        if not cliffs:
            # Nothing to diagnose. Record the fingerprint so the state is
            # current, but do not spend a pipeline run on a clean trailer --
            # demo_control legitimately has zero cliffs on every scan.
            store.set_fingerprint(trailer_id, current)
            continue
        job_id = uuid.uuid4().hex
        store.save_job(job_id, {"job_id": job_id, "trailer_id": trailer_id,
                                "status": "queued", "triggered_by": "watcher"})
        publish(trailer_id, job_id)
        store.set_fingerprint(trailer_id, current)
        triggered.append({"trailer_id": trailer_id, "job_id": job_id, "cliffs": len(cliffs)})
    return triggered


# /health, not /healthz: Google's frontend intercepts /healthz on Cloud Run and
# answers 404 itself without ever routing to the container (the 404 carries no
# x-cloud-trace-context and no "server: Google Frontend" header, unlike a real
# response from this app). Verified live against the deployed revision.
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/pubsub/scan")
def pubsub_scan(envelope: dict, caller: str = Depends(verify_google_identity)) -> dict:
    message = envelope.get("message") or {}
    if message.get("data"):
        try:
            base64.b64decode(message["data"])
        except Exception as exc:
            raise HTTPException(status_code=400, detail="message.data is not base64") from exc

    from ingest.clickhouse_client import get_ingest_client

    try:
        client = get_ingest_client()
    except Exception as exc:  # noqa: BLE001 -- classified and reported below
        return _degraded("connect", exc)

    try:
        triggered = scan(client)
    except Exception as exc:  # noqa: BLE001 -- classified and reported below
        return _degraded("scan", exc)
    finally:
        client.close()

    obs.info(
        "scan complete",
        triggered_count=len(triggered),
        triggered=[t["trailer_id"] for t in triggered],
    )
    return {"triggered": triggered}


def _degraded(stage: str, exc: Exception) -> dict:
    """Report a failed scan without asking Pub/Sub to retry it forever.

    Pub/Sub redelivers on any 5xx. An unreachable database is not something a
    redelivery can fix, so letting the exception escape turned one scheduled
    tick into an unbounded retry loop that wakes the service and bills for it
    until the message expires.

    This is not swallowing the error: it is recorded in Firestore under
    cutpoint_watch/_last_error and logged, so a failed scan stays visible. The
    200 only tells Pub/Sub not to send this same tick again -- the next
    scheduled tick still runs.
    """
    detail = f"{type(exc).__name__}: {exc}"[:500]
    obs.error("scan failed", stage=stage, error=detail, component="watcher")
    try:
        store.save_watch_error(
            {"stage": stage, "error": detail, "component": "watcher"}
        )
    except Exception as store_exc:  # noqa: BLE001 -- never mask the original
        obs.error("could not record the scan failure", error=str(store_exc)[:200])
    return {"status": "degraded", "stage": stage, "error": detail}
