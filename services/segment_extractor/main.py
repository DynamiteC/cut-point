"""FastAPI segment extractor: clips a video window around a retention cliff using
ffmpeg stream copy where possible.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLIPS_DIR = REPO_ROOT / "data" / "clips"

app = FastAPI(title="CutPoint Segment Extractor")


class ExtractRequest(BaseModel):
    video_path: str = Field(..., description="local path or gs:// URI to the source video")
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., gt=0)


class ExtractResponse(BaseModel):
    clip_path: str
    duration_s: float


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


def resolve_local_path(video_path: str) -> Path:
    if video_path.startswith("gs://"):
        raise HTTPException(
            status_code=501,
            detail=(
                "gs:// video sources require GCS_BUCKET download support, not implemented "
                "in this prototype -- provide a local video_path instead"
            ),
        )
    path = Path(video_path)
    if not path.is_absolute():
        path = REPO_ROOT / video_path
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"video not found at {path}")
    return path


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest) -> ExtractResponse:
    source_path = resolve_local_path(req.video_path)
    source_duration = ffprobe_duration(str(source_path))

    start_s = max(0.0, req.start_s)
    end_s = min(source_duration, req.end_s)
    if end_s <= start_s:
        raise HTTPException(
            status_code=400,
            detail=f"invalid clip window: start_s={start_s} end_s={end_s} source_duration={source_duration}",
        )
    clip_duration = end_s - start_s

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    clip_path = CLIPS_DIR / f"{source_path.stem}_{uuid.uuid4().hex[:8]}.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_s),
        "-i",
        str(source_path),
        "-t",
        str(clip_duration),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(clip_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    duration_mismatch = (
        clip_path.exists() and abs(ffprobe_duration(str(clip_path)) - clip_duration) > 0.5
    )
    if result.returncode != 0 or not clip_path.exists() or duration_mismatch:
        # stream copy snaps to the nearest keyframe and can miss the requested
        # window -- fall back to a frame-accurate re-encode.
        cmd_reencode = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_s),
            "-i",
            str(source_path),
            "-t",
            str(clip_duration),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(clip_path),
        ]
        result = subprocess.run(cmd_reencode, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {result.stderr[-500:]}")

    actual_duration = ffprobe_duration(str(clip_path))
    return ExtractResponse(clip_path=str(clip_path), duration_s=actual_duration)
