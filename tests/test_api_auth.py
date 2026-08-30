"""The state-changing endpoints run the full paid pipeline (ClickHouse queries,
ffmpeg extractions, one Gemini video inference per clip). Unauthenticated on
Cloud Run they are an open funnel into the project's Vertex AI bill, so auth
must fail closed and the read-only endpoints must stay public for the static UI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    # Default with no env set at all must also be "required"; assert that first.
    monkeypatch.delenv("CUTPOINT_REQUIRE_AUTH", raising=False)


def test_auth_defaults_to_required_when_unset() -> None:
    from api.auth import auth_required

    assert auth_required() is True


@pytest.mark.parametrize("path", ["/analyze", "/jobs"])
def test_state_changing_endpoints_reject_anonymous_callers(path: str) -> None:
    # Act
    response = client.post(path, json={"trailer_id": "demo_001"})

    # Assert
    assert response.status_code == 401
    assert "bearer" in response.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/analyze", "/jobs"])
def test_state_changing_endpoints_reject_a_forged_token(path: str) -> None:
    # Act
    response = client.post(
        path,
        json={"trailer_id": "demo_001"},
        headers={"Authorization": "Bearer not-a-real-google-token"},
    )

    # Assert: verification failure is a 401, never a 500 leaking a stack trace
    assert response.status_code == 401


def test_pubsub_push_handler_rejects_unsigned_delivery() -> None:
    # A forged POST to the push URL must be indistinguishable from an attacker,
    # not from Cloud Scheduler.
    response = client.post("/pubsub/analyze", json={"message": {"data": "e30="}})
    assert response.status_code == 401


def test_read_only_endpoints_stay_public() -> None:
    # The GitHub Pages UI fetches these cross-origin with no credential.
    assert client.get("/health").status_code == 200
    assert client.get("/trailers").status_code == 200


def test_public_job_status_never_leaks_internals(monkeypatch, tmp_path) -> None:
    """GET /jobs/{id} is unauthenticated so the UI can poll progress. A failed
    job's raw exception text carries database hostnames, ports and connection
    strings, and requested_by carries a caller identity. Neither may be served
    to an anonymous caller.
    """
    from agent.cutpoint_agent import store

    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "JOBS_DIR", tmp_path / "jobs")
    store.save_job(
        "abc123",
        {
            "job_id": "abc123",
            "trailer_id": "demo_001",
            "status": "failed",
            "requested_by": "runtime-sa@example.iam.gserviceaccount.com",
            "error": "OperationalError: HTTPConnectionPool(host='db.internal', port=8443)",
        },
    )

    body = client.get("/jobs/abc123").json()

    assert body["status"] == "failed"
    assert "error" not in body, "raw exception text must not reach an anonymous caller"
    assert "requested_by" not in body, "caller identity must not be public"
    assert "db.internal" not in str(body)


def test_daily_budget_stops_runaway_spend(monkeypatch, tmp_path) -> None:
    """The concurrency cap bounds how FAST money is spent; this bounds how MUCH.
    Every run is ClickHouse queries plus a Gemini video inference per clip.
    """
    from agent.cutpoint_agent import store
    from api import main

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "BUDGET_DIR", tmp_path / "budget")
    monkeypatch.setattr(main, "_MAX_PER_DAY", 2)
    monkeypatch.setattr(main.app.state, "pipeline_runner", lambda t: {"report_path": "x"}, raising=False)

    codes = [
        client.post("/analyze", json={"trailer_id": "demo_001"}).status_code
        for _ in range(4)
    ]

    assert codes[:2] == [200, 200], "runs within budget must succeed"
    assert codes[2:] == [429, 429], "runs past budget must be refused, not billed"


def test_a_failed_run_still_consumes_daily_budget(monkeypatch, tmp_path) -> None:
    """This test previously asserted the opposite, and was wrong.

    A run that fails at the diagnostician has already paid for the ClickHouse
    queries, the ffmpeg extractions and every Gemini call before the one that
    broke. Refunding it made the ceiling soft on exactly the case it exists to
    bound: a repeating failure could spend without limit, because every attempt
    handed its budget back. Only a rejection is refunded, because only a
    rejection does no work.
    """
    from agent.cutpoint_agent import store
    from api import main

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "BUDGET_DIR", tmp_path / "budget")
    monkeypatch.setattr(main, "_MAX_PER_DAY", 2)

    def boom(_trailer_id):
        raise RuntimeError("Vertex AI unavailable after the extractions were paid for")

    monkeypatch.setattr(main.app.state, "pipeline_runner", boom, raising=False)
    for _ in range(2):
        try:
            client.post("/analyze", json={"trailer_id": "demo_001"})
        except RuntimeError:
            pass

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert store.bump_daily_analyses(day, delta=0) == 2, "failed runs must still count"

    monkeypatch.setattr(main.app.state, "pipeline_runner", lambda t: {"report_path": "x"})
    assert client.post("/analyze", json={"trailer_id": "demo_001"}).status_code == 429, (
        "two failed runs spent the budget of two; the third must be refused"
    )


def test_a_rejected_run_is_refunded(monkeypatch, tmp_path) -> None:
    """The over-budget path does no work, so it must not consume budget itself,
    or the counter would run away past the ceiling on every retry.
    """
    from agent.cutpoint_agent import store
    from api import main

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "BUDGET_DIR", tmp_path / "budget")
    monkeypatch.setattr(main, "_MAX_PER_DAY", 1)
    monkeypatch.setattr(main.app.state, "pipeline_runner", lambda t: {"report_path": "x"},
                        raising=False)

    assert client.post("/analyze", json={"trailer_id": "demo_001"}).status_code == 200
    for _ in range(3):
        assert client.post("/analyze", json={"trailer_id": "demo_001"}).status_code == 429

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert store.bump_daily_analyses(day, delta=0) == 1, (
        "rejections must not inflate the counter"
    )


def test_pubsub_redelivery_of_an_inflight_job_does_not_start_a_second_pipeline(
    monkeypatch, tmp_path
) -> None:
    """Pub/Sub is at-least-once. A long pipeline can approach the 600s ack
    deadline, so a redelivery can arrive while the first run is still going. The
    done-check does not catch that (the job is "running", not "done"), so a
    recent in-flight job must be acked and skipped rather than starting a second
    pipeline that double-spends on Gemini.
    """
    import base64
    import json

    from agent.cutpoint_agent import store
    from api import main

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "JOBS_DIR", tmp_path / "jobs")

    store.save_job(
        "job_inflight",
        {
            "job_id": "job_inflight",
            "trailer_id": "demo_001",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
        },
    )

    ran = {"count": 0}

    def _should_not_run(_trailer_id):
        ran["count"] += 1
        return {"report_path": "x"}

    monkeypatch.setattr(main.app.state, "pipeline_runner", _should_not_run, raising=False)

    data = base64.b64encode(
        json.dumps({"trailer_id": "demo_001", "job_id": "job_inflight"}).encode()
    ).decode()
    response = client.post("/pubsub/analyze", json={"message": {"data": data}})

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert ran["count"] == 0, "a redelivery of an in-flight job must not re-run the pipeline"


def test_pubsub_redelivery_of_a_stale_running_job_is_retried(monkeypatch, tmp_path) -> None:
    """A run that crashed without ever recording "failed" leaves a stale
    "running" job. Once its start is older than the in-flight window it must be
    retryable, or the trailer could never be analysed again.
    """
    import base64
    import json
    from datetime import timedelta

    from agent.cutpoint_agent import store
    from api import main

    monkeypatch.setenv("CUTPOINT_REQUIRE_AUTH", "false")
    monkeypatch.setenv("CUTPOINT_STORE", "local")
    monkeypatch.setattr(store, "JOBS_DIR", tmp_path / "jobs")

    stale = datetime.now(UTC) - timedelta(seconds=main._INFLIGHT_WINDOW_S + 60)
    store.save_job(
        "job_stale",
        {
            "job_id": "job_stale",
            "trailer_id": "demo_001",
            "status": "running",
            "started_at": stale.isoformat(),
        },
    )

    ran = {"count": 0}
    monkeypatch.setattr(
        main.app.state,
        "pipeline_runner",
        lambda t: ran.__setitem__("count", ran["count"] + 1) or {"report_path": "x"},
        raising=False,
    )

    data = base64.b64encode(
        json.dumps({"trailer_id": "demo_001", "job_id": "job_stale"}).encode()
    ).decode()
    response = client.post("/pubsub/analyze", json={"message": {"data": data}})

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert ran["count"] == 1, "a stale running job past the window must be retried once"
