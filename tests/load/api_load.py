"""Phase 10.2: API facade load test.

Runs httpx.AsyncClient load tests against the CutPoint FastAPI app.
Starts uvicorn as a subprocess, hammers endpoints at various concurrency levels,
and writes latency percentile results to tests/load/results/api_results.json.

Usage:
    uv run python tests/load/api_load.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

API_HOST = "127.0.0.1"
API_PORT = 8902
BASE_URL = f"http://{API_HOST}:{API_PORT}"

FIXTURE_TRAILER_ID = "demo_001"
TOTAL_REQUESTS = 100
CONCURRENCY_LEVELS = [10, 50, 100]


def ensure_fixture_data() -> None:
    """Write a minimal valid DirectorsNotes JSON fixture if it doesn't already exist."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = REPORTS_DIR / f"{FIXTURE_TRAILER_ID}.json"
    if fixture_path.exists():
        print(f"  Fixture already exists: {fixture_path}")
        return

    fixture = {
        "trailer_id": FIXTURE_TRAILER_ID,
        "cliffs": [
            {
                "second": 22,
                "drop_pct": 0.18,
                "cohorts": ["13-17"],
                "hypothesis": "test",
                "on_screen": "test scene",
            }
        ],
    }
    fixture_path.write_text(json.dumps(fixture, indent=2))
    print(f"  Created fixture: {fixture_path}")


def start_server() -> subprocess.Popen:
    """Start the uvicorn server as a subprocess."""
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        API_HOST,
        "--port",
        str(API_PORT),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def wait_for_server(timeout: float = 30.0) -> None:
    """Poll GET /trailers until the server is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{BASE_URL}/trailers", timeout=2.0)
            if resp.status_code == 200:
                print("  Server is ready.")
                return
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Server did not become ready within {timeout}s")


def kill_server(proc: subprocess.Popen) -> None:
    """Terminate the uvicorn subprocess."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    print("  Server stopped.")


def compute_percentiles(latencies: list[float]) -> dict:
    """Compute p50, p95, p99 from a list of latencies in ms."""
    if not latencies:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}
    sorted_lat = sorted(latencies)
    n = len(sorted_lat)
    p50 = sorted_lat[int(n * 0.50)]
    p95 = sorted_lat[int(n * 0.95)] if n > 1 else sorted_lat[-1]
    p99 = sorted_lat[int(n * 0.99)] if n > 1 else sorted_lat[-1]
    return {"p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "p99_ms": round(p99, 2)}


async def run_load(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    concurrency: int,
    total: int,
    body: dict | None = None,
) -> dict:
    """Run `total` requests at `concurrency` level, returning latency stats."""
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0

    async def single_request() -> None:
        nonlocal errors
        async with semaphore:
            start = time.perf_counter()
            try:
                if method == "GET":
                    resp = await client.get(url, timeout=10.0)
                else:
                    resp = await client.post(url, json=body, timeout=30.0)
                elapsed_ms = (time.perf_counter() - start) * 1000
                if resp.status_code >= 400:
                    errors += 1
                latencies.append(elapsed_ms)
            except Exception:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
                errors += 1

    tasks = [asyncio.create_task(single_request()) for _ in range(total)]
    await asyncio.gather(*tasks)

    stats = compute_percentiles(latencies)
    stats["error_rate"] = round(errors / total, 4) if total > 0 else 0.0
    stats["total_requests"] = total
    stats["concurrency"] = concurrency
    return stats


async def load_test_endpoint(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    label: str,
    concurrency_levels: list[int],
    total: int,
    body: dict | None = None,
) -> list[dict]:
    """Run load test for an endpoint at multiple concurrency levels."""
    results = []
    for conc in concurrency_levels:
        print(f"    {label} @ concurrency={conc}, total={total}...")
        stats = await run_load(client, method, url, conc, total, body)
        print(
            f"      p50={stats['p50_ms']:.1f}ms  "
            f"p95={stats['p95_ms']:.1f}ms  "
            f"p99={stats['p99_ms']:.1f}ms  "
            f"errors={stats['error_rate']*100:.1f}%"
        )
        results.append(stats)
    return results


async def run_all_tests() -> dict:
    """Execute the full load test suite."""
    results: dict = {}

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # GET /trailers
        print("\n  [1/3] Load testing GET /trailers")
        results["trailers_endpoint"] = await load_test_endpoint(
            client,
            "GET",
            "/trailers",
            "GET /trailers",
            CONCURRENCY_LEVELS,
            TOTAL_REQUESTS,
        )

        # GET /report/{id}
        print("\n  [2/3] Load testing GET /report/demo_001")
        results["report_endpoint"] = await load_test_endpoint(
            client,
            "GET",
            f"/report/{FIXTURE_TRAILER_ID}",
            "GET /report",
            CONCURRENCY_LEVELS,
            TOTAL_REQUESTS,
        )

        # POST /analyze: capped at concurrency 3
        # POST /analyze caps at concurrency 3: each call triggers real Gemini/Vertex AI
        # inference. Higher concurrency would burn quota rapidly and incur significant
        # cost. This endpoint is load-tested only for connection handling, not throughput.
        gemini_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        if gemini_key:
            print("\n  [3/3] Load testing POST /analyze (capped concurrency=3)")
            results["analyze_endpoint"] = await load_test_endpoint(
                client,
                "POST",
                "/analyze",
                "POST /analyze",
                [3],
                9,  # Only 9 requests at concurrency 3 to limit cost
                body={"trailer_id": FIXTURE_TRAILER_ID},
            )
        else:
            print("\n  [3/3] Skipping POST /analyze (no Gemini credentials in env)")
            results["analyze_endpoint"] = []

    # Threshold check: p99 < 2000ms at 50 concurrent /trailers
    trailers_50 = next(
        (r for r in results["trailers_endpoint"] if r["concurrency"] == 50), None
    )
    threshold_check = {
        "target": "p99 < 2000ms at 50 concurrent /trailers",
        "actual_p99_ms": trailers_50["p99_ms"] if trailers_50 else None,
        "passed": trailers_50["p99_ms"] < 2000 if trailers_50 else False,
    }
    results["threshold_check"] = threshold_check

    return results


def main() -> None:
    print("=" * 60)
    print("CutPoint API Load Test (Phase 10.2)")
    print("=" * 60)

    # Step 1: Ensure fixture data
    print("\n[Setup] Ensuring fixture data...")
    ensure_fixture_data()

    # Step 2: Start server
    print("\n[Setup] Starting uvicorn server...")
    server_proc = start_server()

    try:
        wait_for_server()

        # Step 3: Run load tests
        print("\n[Load Test] Running...")
        results = asyncio.run(run_all_tests())

        # Step 4: Write results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = RESULTS_DIR / "api_results.json"
        output_path.write_text(json.dumps(results, indent=2))
        print(f"\n[Results] Written to {output_path}")

        # Step 5: Threshold assertion
        threshold = results["threshold_check"]
        print(f"\n[Threshold] {threshold['target']}")
        print(f"  Actual p99: {threshold['actual_p99_ms']}ms")
        if threshold["passed"]:
            print("  PASSED")
        else:
            print("  FAILED: p99 exceeds 2000ms threshold at 50 concurrent /trailers requests")

    finally:
        # Step 6: Cleanup
        print("\n[Cleanup] Stopping server...")
        kill_server(server_proc)

    print("\n" + "=" * 60)
    print("Load test complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
