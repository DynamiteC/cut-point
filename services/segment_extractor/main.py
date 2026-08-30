"""FastAPI segment extractor: clips a video window around a retention cliff using
ffmpeg stream copy where possible.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLIPS_DIR = REPO_ROOT / "data" / "clips"
VIDEOS_DIR = (REPO_ROOT / "data" / "videos").resolve()

def _log(severity: str, message: str, **fields: object) -> None:
    """Local structured logger.

    This service is built from its own directory and its image contains only
    services/segment_extractor. It briefly imported agent.cutpoint_agent.obs,
    which meant the image could no longer start: uvicorn raised
    ModuleNotFoundError on `agent` before serving a request. A standalone
    microservice must not import the agent package. Same Cloud Logging JSON
    shape, ten lines, no dependency.
    """
    if os.environ.get("K_SERVICE"):
        print(json.dumps({"severity": severity, "message": message, **fields}, default=str), flush=True)
    else:
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        print(f"[{severity}] {message}{' ' + extra if extra else ''}", flush=True)


app = FastAPI(title="CutPoint Segment Extractor")


class ExtractRequest(BaseModel):
    video_path: str = Field(..., description="local path or gs:// URI to the source video")
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., gt=0)


class ExtractResponse(BaseModel):
    clip_path: str
    duration_s: float


def ffprobe_duration(path: str, strict: bool = True) -> float | None:
    """Duration in seconds, or None when strict=False and the file is unreadable.

    The duration-mismatch check that decides whether to re-encode must not raise:
    a stream copy that produced a corrupt clip is precisely the case the
    re-encode fallback exists for, and a raising probe turned that into a 500.
    """
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
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        if strict:
            # 500 preserves the documented contract (README error-handling table,
            # chaos test 3). The change here is that the caller now gets a
            # specific ffprobe message instead of an unhandled CalledProcessError.
            # stderr echoes the full input URI. For a signed URL that URI IS the
            # credential, so it is logged for the operator and never returned.
            _log("ERROR", "ffprobe failed", path=str(path), stderr=result.stderr[-300:])
            raise HTTPException(
                status_code=500,
                detail="ffprobe could not read a duration from the source; see server logs",
            ) from None
        return None


def _bucket_name() -> str | None:
    return os.environ.get("GCS_BUCKET") or None


def _split_gs_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri[len("gs://"):]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise HTTPException(status_code=400, detail=f"malformed gs:// URI: {uri}")
    return bucket, key


def _download_from_gcs(uri: str) -> Path:
    """Fetch a source video to a tempfile.

    On Cloud Run there is no data/videos/ in the image, so a local path can
    never resolve. GCS is how the deployed service gets its source at all.

    Confined to the configured bucket on purpose. This service runs as a service
    account holding roles/storage.objectAdmin across the project, so accepting an
    arbitrary bucket turned "extract a clip" into "read any object in the
    project and hand it back", which is a credential-exfiltration primitive if
    any bucket in the project ever holds a key or a dump.
    """
    from google.cloud import storage

    bucket_name, key = _split_gs_uri(uri)
    allowed = _bucket_name()
    if allowed and bucket_name != allowed:
        raise HTTPException(
            status_code=403,
            detail="source bucket is not permitted for this service",
        )
    blob = storage.Client().bucket(bucket_name).blob(key)
    if not blob.exists():
        _log("WARNING", "source object missing", uri=uri)
        raise HTTPException(status_code=404, detail="source video not found")
    suffix = Path(key).suffix or ".mp4"
    fd, temp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    blob.download_to_filename(temp_name)
    return Path(temp_name)


def _upload_clip(local_path: Path) -> str:
    """Return a gs:// URI when a bucket is configured, else the local path.

    The clip is written to this container's ephemeral disk, which the
    diagnostician (a different Cloud Run service) cannot read. Handing back a
    local path there meant every extraction was discarded and every diagnosis
    failed. Local runs keep the plain path so `make demo` is unchanged.
    """
    bucket_name = _bucket_name()
    if not bucket_name:
        return str(local_path)
    from google.cloud import storage

    key = f"clips/{local_path.name}"
    storage.Client().bucket(bucket_name).blob(key).upload_from_filename(
        str(local_path), content_type="video/mp4"
    )
    return f"gs://{bucket_name}/{key}"


def resolve_local_path(video_path: str) -> Path:
    if video_path.startswith("gs://"):
        return _download_from_gcs(video_path)
    path = Path(video_path)
    if not path.is_absolute():
        path = REPO_ROOT / video_path
    path = path.resolve()

    if not path.is_relative_to(VIDEOS_DIR):
        raise HTTPException(
            status_code=400,
            detail="video_path must resolve inside the videos directory -- refusing path outside it",
        )
    if not path.exists():
        _log("WARNING", "source video missing", path=str(path))
        raise HTTPException(status_code=404, detail="source video not found")
    return path


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest) -> ExtractResponse:
    source_path = resolve_local_path(req.video_path)
    source_is_temp = req.video_path.startswith("gs://")
    try:
        return _extract(req, source_path)
    finally:
        if source_is_temp:
            source_path.unlink(missing_ok=True)


def _extract(req: ExtractRequest, source_path: Path) -> ExtractResponse:
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
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # Non-strict: a stream copy that produced an unreadable clip is exactly what
    # the re-encode fallback below is for, so an unprobeable file counts as a
    # mismatch rather than raising past the fallback as a 500.
    copied_duration = ffprobe_duration(str(clip_path), strict=False) if clip_path.exists() else None
    duration_mismatch = copied_duration is None or abs(copied_duration - clip_duration) > 0.5
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
        result = subprocess.run(cmd_reencode, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            _log("ERROR", "ffmpeg failed", stderr=result.stderr[-500:])
            raise HTTPException(status_code=500, detail="ffmpeg failed; see server logs")

    actual_duration = ffprobe_duration(str(clip_path))
    return ExtractResponse(clip_path=_upload_clip(clip_path), duration_s=actual_duration)
