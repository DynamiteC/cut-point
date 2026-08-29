"""Thin REST facade for the frontend (see TASK.md section 8 / docs/frontend-spec.md).

Public, read-only:
  GET  /trailers                 -> known trailer ids
  GET  /report/{trailer_id}      -> DirectorsNotes JSON
  GET  /report/{trailer_id}/html -> rendered HTML
  GET  /jobs/{job_id}            -> job status
  GET  /health                   -> liveness

Authenticated, state-changing (each call runs the full paid pipeline):
  POST /analyze {trailer_id}     -> runs synchronously -> {report_id}
  POST /jobs {trailer_id}        -> enqueues via Pub/Sub -> {job_id}
  POST /pubsub/analyze           -> Pub/Sub push handler
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent.cutpoint_agent import store
from api.auth import verify_google_identity

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "reports"
GROUND_TRUTH_PATH = REPO_ROOT / "data" / "ground_truth.json"

# trailer_id flows into filesystem paths (data/reports/{trailer_id}.json) and into SQL query
# text built by the analyst LLM (sql/analysis/*.sql templates) -- restrict to a safe charset
# to rule out path traversal and SQL-injection-shaped payloads at the API boundary.
TRAILER_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"
JOB_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

ANALYZE_TOPIC = os.environ.get("CUTPOINT_ANALYZE_TOPIC", "cutpoint-analyze")

# FastAPI dispatches sync `def` handlers into a 40-slot threadpool, so an
# unguarded /analyze allows 40 concurrent pipelines per instance, each one N
# Gemini video calls. Cap it and shed load instead.
_MAX_CONCURRENT = int(os.environ.get("CUTPOINT_MAX_CONCURRENT_PIPELINES", "2"))
_pipeline_slots = threading.Semaphore(_MAX_CONCURRENT)

app = FastAPI(title="CutPoint API")

# The state-changing routes authenticate with a bearer token, not a cookie, so a
# permissive origin list is not itself an escalation path. Deploys still set
# CUTPOINT_ALLOWED_ORIGINS to the UI origin.
_origins = [o.strip() for o in os.environ.get("CUTPOINT_ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class AnalyzeRequest(BaseModel):
    trailer_id: str = Field(..., pattern=TRAILER_ID_PATTERN)


class AnalyzeResponse(BaseModel):
    report_id: str


class JobResponse(BaseModel):
    job_id: str
    status: str


def default_pipeline_runner(trailer_id: str) -> dict:
    """Runs the real ADK pipeline synchronously. Overridable via
    app.state.pipeline_runner for tests (see TASK.md Phase 8: pipeline mocked).
    """
    import asyncio

    from agent.run_pipeline import run_live

    return asyncio.run(run_live(trailer_id))


def get_pipeline_runner() -> Callable[[str], dict]:
    return getattr(app.state, "pipeline_runner", default_pipeline_runner)


def _run_pipeline_guarded(trailer_id: str) -> dict:
    if not _pipeline_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"at capacity ({_MAX_CONCURRENT} concurrent analyses); retry shortly",
        )
    try:
        return get_pipeline_runner()(trailer_id)
    finally:
        _pipeline_slots.release()


# /health, not /healthz: Google's frontend intercepts /healthz on Cloud Run and
# answers 404 itself without ever routing to the container (the 404 carries no
# x-cloud-trace-context and no "server: Google Frontend" header, unlike a real
# response from this app). Verified live against the deployed revision.
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/trailers")
def list_trailers() -> list[str]:
    if not GROUND_TRUTH_PATH.exists():
        return []
    return list(json.loads(GROUND_TRUTH_PATH.read_text()).keys())


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, caller: str = Depends(verify_google_identity)) -> AnalyzeResponse:
    final_state = _run_pipeline_guarded(req.trailer_id)
    if not final_state.get("report_path") and not final_state.get("directors_notes"):
        raise HTTPException(status_code=500, detail="pipeline did not produce a report")
    return AnalyzeResponse(report_id=req.trailer_id)


@app.post("/jobs", response_model=JobResponse)
def enqueue_job(req: AnalyzeRequest, caller: str = Depends(verify_google_identity)) -> JobResponse:
    """Hand the work to Pub/Sub and return immediately. The push subscription
    delivers it back to /pubsub/analyze, which runs the pipeline inline.
    """
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "trailer_id": req.trailer_id,
        "status": "queued",
        "requested_by": caller,
        "created_at": datetime.now(UTC).isoformat(),
    }
    store.save_job(job_id, job)
    _publish(req.trailer_id, job_id)
    return JobResponse(job_id=job_id, status="queued")


def _publish(trailer_id: str, job_id: str) -> None:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(os.environ["GOOGLE_CLOUD_PROJECT"], ANALYZE_TOPIC)
    publisher.publish(
        topic, json.dumps({"trailer_id": trailer_id, "job_id": job_id}).encode()
    ).result(timeout=30)


# Fields safe to hand to an anonymous caller. Deliberately a allowlist, not a
# denylist: `error` carries raw exception text (database hostnames, ports,
# connection strings) and `requested_by` carries a caller identity, and neither
# belongs in a public response. The UI only needs progress.
PUBLIC_JOB_FIELDS = ("job_id", "trailer_id", "status", "created_at", "started_at", "finished_at")


@app.get("/jobs/{job_id}")
def get_job(job_id: str = PathParam(..., pattern=JOB_ID_PATTERN)) -> dict:
    job = store.load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    public = {k: v for k, v in job.items() if k in PUBLIC_JOB_FIELDS}
    if not public:
        # A stored document with no public fields is not a job anyone can poll
        # (the watcher's old _last_error record is one). Do not answer 200 {}.
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    if job.get("status") == "failed":
        # Say that it failed without saying what the internals were.
        public["detail"] = "analysis failed; see server logs"
    return public


@app.post("/pubsub/analyze")
def pubsub_analyze(envelope: dict, caller: str = Depends(verify_google_identity)) -> dict:
    """Pub/Sub push handler.

    Runs the pipeline INLINE and only returns once it is done. Acking early and
    continuing in a background task does not work here: Cloud Run throttles CPU
    to near zero once the response is sent, so the pipeline would stall. The
    subscription is created with --ack-deadline=600 to cover the runtime.
    """
    import base64

    message = envelope.get("message") or {}
    raw = message.get("data")
    if not raw:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        payload = json.loads(base64.b64decode(raw))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="message.data is not valid JSON") from exc

    trailer_id = payload.get("trailer_id", "")
    job_id = payload.get("job_id") or uuid.uuid4().hex
    if not trailer_id or not _valid_id(trailer_id):
        raise HTTPException(status_code=400, detail="invalid trailer_id")

    existing = store.load_job(job_id)
    if existing and existing.get("status") == "done":
        # Pub/Sub is at-least-once; a redelivery must not re-run the pipeline
        # and re-spend on Gemini.
        return {"job_id": job_id, "status": "done", "note": "already complete"}

    store.save_job(job_id, {"job_id": job_id, "trailer_id": trailer_id, "status": "running",
                            "started_at": datetime.now(UTC).isoformat()})
    try:
        _run_pipeline_guarded(trailer_id)
    except Exception as exc:
        store.save_job(job_id, {"job_id": job_id, "trailer_id": trailer_id, "status": "failed",
                                "error": str(exc)[:500],
                                "finished_at": datetime.now(UTC).isoformat()})
        raise
    store.save_job(job_id, {"job_id": job_id, "trailer_id": trailer_id, "status": "done",
                            "finished_at": datetime.now(UTC).isoformat()})
    return {"job_id": job_id, "status": "done"}


def _valid_id(value: str) -> bool:
    return 0 < len(value) <= 64 and all(c.isalnum() or c in "_-" for c in value)


@app.get("/report/{trailer_id}")
def get_report(trailer_id: str = PathParam(..., pattern=TRAILER_ID_PATTERN)) -> dict:
    if store.using_firestore():
        notes = store.load_report(trailer_id)
        if notes is None:
            raise HTTPException(status_code=404, detail=f"no report found for {trailer_id}")
        return notes
    report_path = REPORTS_DIR / f"{trailer_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"no report found for {trailer_id}")
    return json.loads(report_path.read_text())


@app.get("/report/{trailer_id}/html", response_class=HTMLResponse)
def get_report_html(trailer_id: str = PathParam(..., pattern=TRAILER_ID_PATTERN)) -> str:
    html_path = REPORTS_DIR / f"{trailer_id}.html"
    if html_path.exists():
        return html_path.read_text()

    # Render in-memory and return the string. The previous version wrote the
    # HTML file from this GET handler, so two concurrent requests for an
    # unrendered report raced: one truncated the file the other was reading and
    # a judge could be served a half-written page. A read path should not write.
    from agent.cutpoint_agent.schemas import DirectorsNotes
    from report.render import render_html

    notes_dict = get_report(trailer_id)
    return render_html(DirectorsNotes.model_validate(notes_dict))
