"""The state-changing endpoints run the full paid pipeline (ClickHouse queries,
ffmpeg extractions, one Gemini video inference per clip). Unauthenticated on
Cloud Run they are an open funnel into the project's Vertex AI bill, so auth
must fail closed and the read-only endpoints must stay public for the static UI.
"""

from __future__ import annotations

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
