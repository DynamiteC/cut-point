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
    assert client.get("/healthz").status_code == 200
    assert client.get("/trailers").status_code == 200
