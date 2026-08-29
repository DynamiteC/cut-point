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


def test_malformed_gcs_uri_is_rejected():
    # gs:// is now a supported source. A bucket with no object key is not.
    response = client.post(
        "/extract",
        json={"video_path": "gs://bucket-only", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 400
    assert "malformed" in response.json()["detail"]


def test_missing_gcs_object_is_a_404_not_a_crash(monkeypatch):
    class FakeBlob:
        def exists(self):
            return False

    class FakeBucket:
        def blob(self, key):
            return FakeBlob()

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    import google.cloud.storage as gcs

    monkeypatch.setattr(gcs, "Client", lambda *a, **k: FakeClient())
    response = client.post(
        "/extract",
        json={"video_path": "gs://bucket/missing.mp4", "start_s": 0, "end_s": 5},
    )
    assert response.status_code == 404


def test_local_clip_path_is_returned_when_no_bucket_is_configured(monkeypatch):
    # `make demo` and the tests must keep working with no cloud access at all.
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    from services.segment_extractor.main import _upload_clip

    assert _upload_clip(Path("/tmp/x.mp4")) == "/tmp/x.mp4"


def test_non_strict_probe_returns_none_instead_of_raising(tmp_path):
    """The duration-mismatch check must not raise.

    A stream copy that produced an unreadable clip is exactly the case the
    re-encode fallback exists for; a raising probe escaped past the fallback as
    a 500 and the fallback could never run.
    """
    from services.segment_extractor.main import ffprobe_duration

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")

    assert ffprobe_duration(str(corrupt), strict=False) is None


def test_strict_probe_still_fails_loud_with_an_ffprobe_message(tmp_path):
    from fastapi import HTTPException

    from services.segment_extractor.main import ffprobe_duration

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")

    with pytest.raises(HTTPException) as excinfo:
        ffprobe_duration(str(corrupt), strict=True)
    assert excinfo.value.status_code == 500
    assert "ffprobe" in excinfo.value.detail
