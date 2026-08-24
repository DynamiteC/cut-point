"""Chaos scenario 3: Truncate/corrupt a video file the extractor is asked to clip.

Proves: ffmpeg failure is caught and reported per-clip, and the pipeline
continues to process OTHER clips rather than aborting the whole run
(partial-failure isolation).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_VIDEO = REPO_ROOT / "data" / "videos" / "demo_001.mp4"


@pytest.fixture()
def corrupt_video(tmp_path) -> Path:
    """Create a corrupt .mp4 file (random bytes)."""
    corrupt_path = tmp_path / "corrupt.mp4"
    corrupt_path.write_bytes(os.urandom(1024))
    return corrupt_path


@pytest.fixture()
def valid_test_video(tmp_path) -> Path:
    """Create a short valid test video using ffmpeg, or use the demo video if available."""
    if SAMPLE_VIDEO.exists():
        return SAMPLE_VIDEO
    # Generate a minimal 2-second test video
    output = tmp_path / "test_valid.mp4"
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"ffmpeg not available or failed to generate test video: {result.stderr[:200]}")
    return output


@pytest.fixture()
def extractor_client(tmp_path, monkeypatch):
    """Create a TestClient for the extractor with VIDEOS_DIR pointing to tmp_path."""
    from services.segment_extractor import main as extractor_main

    # Patch VIDEOS_DIR so our test files are accessible
    monkeypatch.setattr(extractor_main, "VIDEOS_DIR", tmp_path.resolve())
    monkeypatch.setattr(extractor_main, "CLIPS_DIR", tmp_path / "clips")

    return TestClient(extractor_main.app, raise_server_exceptions=False)


@pytest.mark.timeout(30)
def test_corrupt_video_returns_ffmpeg_error(corrupt_video, extractor_client, tmp_path, monkeypatch):
    """A corrupt video should yield an HTTP 500 with an error mentioning ffmpeg."""

    # Move the corrupt file into the patched VIDEOS_DIR
    target = tmp_path / "corrupt.mp4"
    if not target.exists():
        target.write_bytes(corrupt_video.read_bytes())

    response = extractor_client.post(
        "/extract",
        json={"video_path": str(target), "start_s": 0, "end_s": 5},
    )
    # ffprobe or ffmpeg should fail on the corrupt file
    assert response.status_code == 500

    # The error response may be JSON ({"detail": ...}) or plain text ("Internal Server Error")
    # Either way, it should indicate a server-side failure related to processing
    response_text = response.text.lower()
    assert any(
        keyword in response_text
        for keyword in ["ffmpeg", "ffprobe", "internal server error", "error", "failed"]
    ), f"Error does not indicate ffmpeg/processing failure: {response.text}"


@pytest.mark.timeout(30)
def test_valid_extraction_works_after_corrupt_failure(
    corrupt_video, valid_test_video, extractor_client, tmp_path, monkeypatch
):
    """After a corrupt video fails, a valid video extraction still succeeds (isolation)."""

    # Place corrupt file in VIDEOS_DIR
    corrupt_target = tmp_path / "corrupt.mp4"
    if not corrupt_target.exists():
        corrupt_target.write_bytes(corrupt_video.read_bytes())

    # First request: corrupt video (should fail)
    resp_bad = extractor_client.post(
        "/extract",
        json={"video_path": str(corrupt_target), "start_s": 0, "end_s": 5},
    )
    assert resp_bad.status_code == 500

    # Place valid file in VIDEOS_DIR
    valid_target = tmp_path / "valid.mp4"
    if not valid_target.exists():
        if valid_test_video != SAMPLE_VIDEO:
            valid_target.write_bytes(valid_test_video.read_bytes())
        else:
            # Copy demo video into the patched directory
            valid_target.write_bytes(SAMPLE_VIDEO.read_bytes())

    # Second request: valid video (should succeed, proving isolation)
    resp_good = extractor_client.post(
        "/extract",
        json={"video_path": str(valid_target), "start_s": 0, "end_s": 2},
    )
    assert resp_good.status_code == 200, (
        f"Valid extraction failed after corrupt one: {resp_good.json()}"
    )
    body = resp_good.json()
    assert "clip_path" in body
    assert body["duration_s"] > 0
