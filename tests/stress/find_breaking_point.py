"""Stress test: ramps concurrency to find the breaking point of CutPoint services.

Part A: API facade (GET /report/demo_001) - linear ramp +10 concurrent per step
Part B: Segment extractor (/extract) - linear ramp +2 concurrent per step

Run with: uv run python tests/stress/find_breaking_point.py
"""

from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "reports"
VIDEOS_DIR = REPO_ROOT / "data" / "videos"
DOCS_PERF_DIR = REPO_ROOT / "docs" / "perf"

API_PORT = 8903
EXTRACTOR_PORT = 8904

# Part A config
API_CONCURRENCY_START = 10
API_CONCURRENCY_STEP = 10
API_CONCURRENCY_MAX = 500
API_REQUESTS_PER_LEVEL = 50
API_ERROR_RATE_THRESHOLD = 0.05  # 5%
API_P99_THRESHOLD_MS = 10_000  # 10 seconds

# Part B config
EXTRACTOR_CONCURRENCY_START = 2
EXTRACTOR_CONCURRENCY_STEP = 2
EXTRACTOR_REQUESTS_PER_LEVEL = 10
EXTRACTOR_LATENCY_MULTIPLIER = 3  # stop when median latency > 3x baseline


def ensure_fixture_report() -> None:
    """Create a minimal report fixture for GET /report/demo_001 if it doesn't exist."""
    report_path = REPORTS_DIR / "demo_001.json"
    if report_path.exists():
        return
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fixture = {
        "trailer_id": "demo_001",
        "retention_cliffs": [
            {"second": 15, "drop_pct": 12.5, "severity": "moderate"},
            {"second": 42, "drop_pct": 18.2, "severity": "severe"},
        ],
        "summary": "Stress test fixture report for demo_001",
    }
    report_path.write_text(json.dumps(fixture, indent=2))
    print(f"  Created fixture report at {report_path}")


def ensure_test_video() -> str:
    """Return the path to a test video, creating a minimal one if needed."""
    demo_path = VIDEOS_DIR / "demo_001.mp4"
    if demo_path.exists():
        return str(demo_path)
    stress_path = VIDEOS_DIR / "_stress_test.mp4"
    if stress_path.exists():
        return str(stress_path)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    print("  Creating minimal test video with ffmpeg...")
    subprocess.run(
        [
            "ffmpeg",
            "-f", "lavfi",
            "-i", "testsrc=duration=2:size=320x240:rate=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(stress_path),
        ],
        capture_output=True,
        check=True,
    )
    return str(stress_path)


def start_service(module: str, port: int) -> subprocess.Popen:
    """Start a FastAPI service as a subprocess."""
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            module,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "error",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def wait_for_health(port: int, path: str = "/health", timeout: float = 15.0) -> bool:
    """Poll a health endpoint until it responds 200 or timeout."""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}{path}"
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.3)
    return False


def percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_values):
        return sorted_values[-1]
    d = k - f
    return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])


async def _api_request(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> tuple[float, bool]:
    """Single GET /report/demo_001 request, returns (latency_ms, success)."""
    async with sem:
        t0 = time.perf_counter()
        try:
            resp = await client.get("/report/demo_001")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return elapsed_ms, resp.status_code == 200
        except (httpx.HTTPError, OSError):
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return elapsed_ms, False


async def _extractor_request(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    payload: dict,
) -> float:
    """Single POST /extract request, returns latency_ms."""
    async with sem:
        t0 = time.perf_counter()
        try:
            await client.post("/extract", json=payload)
            return (time.perf_counter() - t0) * 1000
        except (httpx.HTTPError, OSError):
            return (time.perf_counter() - t0) * 1000


async def run_api_stress(port: int) -> int:
    """Part A: ramp concurrency on GET /report/demo_001 until failure threshold."""
    base_url = f"http://127.0.0.1:{port}"
    last_successful = 0

    print("\n--- Part A: API Stress Test (GET /report/demo_001) ---")

    concurrency = API_CONCURRENCY_START
    while concurrency <= API_CONCURRENCY_MAX:
        print(f"  Concurrency: {concurrency} ...", end=" ", flush=True)

        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            sem = asyncio.Semaphore(concurrency)
            tasks = [
                asyncio.create_task(_api_request(client, sem))
                for _ in range(API_REQUESTS_PER_LEVEL)
            ]
            results = await asyncio.gather(*tasks)

        latencies: list[float] = []
        errors = 0
        for elapsed_ms, success in results:
            latencies.append(elapsed_ms)
            if not success:
                errors += 1

        total = API_REQUESTS_PER_LEVEL
        error_rate = errors / total
        sorted_latencies = sorted(latencies)
        p99 = percentile(sorted_latencies, 99)

        print(f"p99={p99:.0f}ms  errors={errors}/{total} ({error_rate*100:.1f}%)")

        if error_rate > API_ERROR_RATE_THRESHOLD or p99 > API_P99_THRESHOLD_MS:
            print(f"  STOP: threshold breached at concurrency {concurrency}")
            break

        last_successful = concurrency
        concurrency += API_CONCURRENCY_STEP

    if last_successful == 0:
        last_successful = API_CONCURRENCY_START  # report minimum if first level fails
        print("  WARNING: first concurrency level already exceeded thresholds")

    print(f"  API ceiling: {last_successful} concurrent requests")
    return last_successful


async def run_extractor_stress(port: int, video_path: str) -> int:
    """Part B: ramp concurrency on /extract until latency > 3x baseline."""
    base_url = f"http://127.0.0.1:{port}"
    baseline_median: float | None = None
    last_acceptable = 0

    # Use relative path from REPO_ROOT for the extractor
    rel_video = str(Path(video_path).relative_to(REPO_ROOT))
    payload = {"video_path": rel_video, "start_s": 0.5, "end_s": 1.0}

    print("\n--- Part B: Segment Extractor Stress Test (/extract) ---")

    concurrency = EXTRACTOR_CONCURRENCY_START
    while True:
        print(f"  Concurrency: {concurrency} ...", end=" ", flush=True)

        async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
            sem = asyncio.Semaphore(concurrency)
            tasks = [
                asyncio.create_task(_extractor_request(client, sem, payload))
                for _ in range(EXTRACTOR_REQUESTS_PER_LEVEL)
            ]
            latencies = await asyncio.gather(*tasks)

        current_median = median(latencies) if latencies else 0.0

        if baseline_median is None:
            baseline_median = current_median
            print(f"median={current_median:.0f}ms (baseline)")
        else:
            ratio = current_median / baseline_median if baseline_median > 0 else 999
            print(f"median={current_median:.0f}ms ({ratio:.1f}x baseline)")

            if current_median > baseline_median * EXTRACTOR_LATENCY_MULTIPLIER:
                print(
                    f"  STOP: latency exceeded {EXTRACTOR_LATENCY_MULTIPLIER}x"
                    f" baseline at concurrency {concurrency}"
                )
                break

        last_acceptable = concurrency
        concurrency += EXTRACTOR_CONCURRENCY_STEP

    if last_acceptable == 0:
        last_acceptable = EXTRACTOR_CONCURRENCY_START
        print("  WARNING: first concurrency level already exceeded threshold")

    print(f"  Extractor ceiling: {last_acceptable} concurrent extractions")
    return last_acceptable


def generate_report(api_ceiling: int, extractor_ceiling: int) -> None:
    """Write docs/perf/stress-report.md."""
    DOCS_PERF_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DOCS_PERF_DIR / "stress-report.md"

    content = f"""# Stress Test Report

## Results

| Metric | Value |
|--------|-------|
| API (GET /report) ceiling | {api_ceiling} concurrent requests |
| Segment extractor ceiling | {extractor_ceiling} concurrent extractions |

## Methodology
- API: linear ramp +10 concurrent per step, stop on >5% errors or p99 >10s
- Extractor: linear ramp +2 concurrent per step, stop on latency >3x baseline
"""
    report_path.write_text(content)
    print(f"\n  Report written to {report_path}")


def main() -> None:
    processes: list[subprocess.Popen] = []

    def cleanup(*_args: object) -> None:
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                p.kill()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        print("=== CutPoint Stress Test ===\n")

        # --- Part A setup ---
        print("Starting API server on port", API_PORT, "...")
        ensure_fixture_report()
        api_proc = start_service("api.main:app", API_PORT)
        processes.append(api_proc)

        if not wait_for_health(API_PORT, "/trailers"):
            print("ERROR: API server failed to start. Aborting.")
            cleanup()
            sys.exit(1)
        print("  API server ready.")

        api_ceiling = asyncio.run(run_api_stress(API_PORT))

        # --- Part B setup ---
        print("\nStarting segment extractor on port", EXTRACTOR_PORT, "...")
        video_path = ensure_test_video()
        ext_proc = start_service("services.segment_extractor.main:app", EXTRACTOR_PORT)
        processes.append(ext_proc)

        if not wait_for_health(EXTRACTOR_PORT, "/health"):
            print("ERROR: Segment extractor failed to start. Aborting.")
            cleanup()
            sys.exit(1)
        print("  Segment extractor ready.")

        extractor_ceiling = asyncio.run(run_extractor_stress(EXTRACTOR_PORT, video_path))

        # --- Report ---
        generate_report(api_ceiling, extractor_ceiling)
        print("\n=== Stress Test Complete ===")

    finally:
        cleanup()


if __name__ == "__main__":
    main()
