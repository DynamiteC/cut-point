"""Phase 4 gate: starts the segment extractor service (via TestClient), extracts a
10s clip from the sample video and asserts ffprobe duration within +/-0.5s, then a
3s clip near end-of-file (boundary case).
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.segment_extractor.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VIDEO = REPO_ROOT / "data" / "videos" / "demo_001.mp4"

client = TestClient(app)


def ffprobe_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


@pytest.fixture(scope="module", autouse=True)
def require_sample_video():
    if not SAMPLE_VIDEO.exists():
        pytest.skip("data/videos/demo_001.mp4 not found -- run scripts/fetch_sample_video.sh first")


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_extract_10s_clip():
    response = client.post(
        "/extract",
        json={"video_path": str(SAMPLE_VIDEO), "start_s": 0, "end_s": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert abs(body["duration_s"] - 10.0) <= 0.5
    assert Path(body["clip_path"]).exists()


def test_extract_boundary_clip_near_end_of_file():
    source_duration = ffprobe_duration(str(SAMPLE_VIDEO))
    start_s = source_duration - 3
    response = client.post(
        "/extract",
        json={"video_path": str(SAMPLE_VIDEO), "start_s": start_s, "end_s": source_duration + 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert abs(body["duration_s"] - 3.0) <= 0.5


def test_extract_path_traversal_outside_videos_dir_rejected():
    response = client.post(
        "/extract",
        json={"video_path": "/etc/passwd", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 400

    response = client.post(
        "/extract",
        json={"video_path": "../../../../etc/passwd", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 400


def test_extract_missing_video_returns_404():
    response = client.post(
        "/extract",
        json={"video_path": "data/videos/does_not_exist.mp4", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 404


def test_extract_gcs_uri_returns_actionable_error():
    response = client.post(
        "/extract",
        json={"video_path": "gs://bucket/video.mp4", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 501
