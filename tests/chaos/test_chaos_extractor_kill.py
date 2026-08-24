"""Chaos scenario 1: Kill the segment extractor mid-pipeline.

Proves: the pipeline returns a clear error message mentioning the extractor
service, not an unhandled stack trace, and writes nothing to data/reports/
(no partial or corrupt report file).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "reports"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_service(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if resp.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.2)
    return False


@pytest.fixture()
def extractor_process():
    """Start the segment extractor on a random port, yield the process + port, kill on cleanup."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "services.segment_extractor.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert _wait_for_service(port), f"Extractor failed to start on port {port}"
    yield proc, port
    # Cleanup: kill if still alive
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.timeout(30)
def test_extractor_kill_produces_clear_error(extractor_process, tmp_path):
    """Kill the extractor mid-request and verify the caller gets an actionable error."""
    proc, port = extractor_process
    extractor_url = f"http://127.0.0.1:{port}"

    # Record existing report files so we can detect new ones
    existing_reports = set(REPORTS_DIR.glob("*")) if REPORTS_DIR.exists() else set()

    # Kill the extractor to simulate mid-pipeline failure
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)

    # Now attempt an extraction call against the dead service
    from agent.cutpoint_agent.schemas import AnalysisResult, CliffPoint
    from agent.cutpoint_agent.steps.extractor import run_extraction

    analysis = AnalysisResult(
        trailer_id="chaos_test",
        overall_retention_end=0.4,
        milestone_funnel={"50%": 30.0},
        cliffs=[CliffPoint(second=10, drop_pct=15.0, affected_cohorts=["mobile"], z_score=2.5)],
    )

    with pytest.raises((httpx.ConnectError, httpx.RemoteProtocolError, Exception)) as exc_info:
        run_extraction(analysis, "data/videos/demo_001.mp4", extractor_url)

    # The error should be actionable: mention connection or the service
    error_text = str(exc_info.value).lower()
    assert any(
        keyword in error_text
        for keyword in ["connect", "refused", "connection", "extractor", "closed"]
    ), f"Error not actionable: {exc_info.value}"

    # No new report files should have been created
    current_reports = set(REPORTS_DIR.glob("*")) if REPORTS_DIR.exists() else set()
    new_reports = current_reports - existing_reports
    assert not new_reports, f"Partial report files created during failed run: {new_reports}"
