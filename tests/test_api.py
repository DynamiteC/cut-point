"""Phase 8 gate: httpx tests against the facade with the pipeline mocked --
/trailers, /analyze, /report round-trip.
"""

import json

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_pipeline_and_data(tmp_path, monkeypatch):
    fake_ground_truth = {"demo_001": {"duration_s": 90, "cliffs": []}}
    fake_gt_path = tmp_path / "ground_truth.json"
    fake_gt_path.write_text(json.dumps(fake_ground_truth))
    monkeypatch.setattr("api.main.GROUND_TRUTH_PATH", fake_gt_path)

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr("api.main.REPORTS_DIR", reports_dir)

    def fake_pipeline_runner(trailer_id: str) -> dict:
        report_path = reports_dir / f"{trailer_id}.json"
        notes = {
            "trailer_id": trailer_id,
            "title": "Demo One",
            "duration_s": 90,
            "analyzed_at": "2026-08-19T00:00:00Z",
            "overall_retention_end": 0.35,
            "milestone_funnel": {"completed": 0.35},
            "cliffs": [],
            "executive_summary": "no significant cliffs",
        }
        report_path.write_text(json.dumps(notes))
        return {"report_path": str(report_path), "directors_notes": notes}

    app.state.pipeline_runner = fake_pipeline_runner
    yield
    del app.state.pipeline_runner


def test_list_trailers():
    response = client.get("/trailers")
    assert response.status_code == 200
    assert response.json() == ["demo_001"]


def test_analyze_and_report_round_trip():
    analyze_response = client.post("/analyze", json={"trailer_id": "demo_001"})
    assert analyze_response.status_code == 200
    assert analyze_response.json() == {"report_id": "demo_001"}

    report_response = client.get("/report/demo_001")
    assert report_response.status_code == 200
    body = report_response.json()
    assert body["trailer_id"] == "demo_001"
    assert body["executive_summary"] == "no significant cliffs"


def test_report_html_renders_from_json():
    client.post("/analyze", json={"trailer_id": "demo_001"})
    response = client.get("/report/demo_001/html")
    assert response.status_code == 200
    assert "<svg" in response.text


def test_report_not_found_returns_404():
    response = client.get("/report/nonexistent_trailer")
    assert response.status_code == 404


def test_cors_allows_all_origins():
    response = client.options(
        "/trailers",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "*"
