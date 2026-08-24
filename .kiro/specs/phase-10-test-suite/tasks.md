# Implementation Plan: Phase 10 Test Suite

## Overview
Phase 10 adds load, smoke, soak, chaos, and stress test infrastructure to CutPoint, producing demo-ready artifacts for the hackathon submission. All work is additive and does not touch Phase 0-9 files.

## Tasks

- [ ] 1. Add dev dependencies (matplotlib, psutil)
  - Add matplotlib>=3.9.0 to [dependency-groups] dev in pyproject.toml
  - Add psutil>=6.0.0 to [dependency-groups] dev in pyproject.toml
  - Run `uv sync` to install

- [ ] 2. Create smoke test script (Phase 10.1)
  - Create `scripts/smoke.sh` that starts local ClickHouse, verifies SELECT 1, mcp-clickhouse tool listing, segment extractor /health, API /trailers, and pipeline --dry-run
  - Traps EXIT to kill all background processes
  - Each check prints PASS/FAIL, total runtime under 60s
  - Add `smoke` target to Makefile: `bash scripts/smoke.sh`

- [ ] 3. Create load test for ClickHouse ingest (Phase 10.2)
  - Create `tests/load/__init__.py` and `tests/load/ingest_load.py`
  - Generate synthetic batches of 10k/50k/100k rows, insert via clickhouse-connect with timing
  - Repeat each batch size 5 times, compute p50/p95/p99 insert latency and rows/sec throughput
  - Run retention_curve.sql and changepoints.sql concurrently (ThreadPoolExecutor) for 3 trailers x 20 iterations
  - Compute p50/p95/p99 query latency, write results to tests/load/results/ingest_results.json

- [ ] 4. Create load test for API facade (Phase 10.2)
  - Create `tests/load/api_load.py`
  - Use httpx.AsyncClient with asyncio.Semaphore for concurrency control
  - Hit GET /trailers and GET /report/{id} at 10/50/100 concurrent requests
  - Create fixture report JSON files for self-contained testing
  - Compute p50/p95/p99 and error rate, cap POST /analyze at concurrency 3 (Gemini quota comment)
  - Assert p99 under 2s at 50 concurrent /trailers requests
  - Write results to tests/load/results/api_results.json

- [ ] 5. Create load report generator and Makefile target (Phase 10.2)
  - Create `scripts/load_report.py` that reads both result JSONs
  - Generate docs/perf/load-report.md with formatted tables
  - Generate docs/perf/load-chart.png via matplotlib (latency-vs-concurrency)
  - Document ClickHouse tier and threshold choice
  - Create `docs/perf/` directory
  - Add `load-test` target to Makefile

- [ ] 6. Create stress test (Phase 10.3)
  - Create `tests/stress/__init__.py` and `tests/stress/find_breaking_point.py`
  - Linear-ramp concurrency on GET /report/{id}: 10 to 500, stop on error_rate > 5% or p99 > 10s
  - Stress segment extractor with overlapping /extract: stop when latency exceeds 3x baseline
  - Generate docs/perf/stress-report.md with concurrency ceiling numbers
  - Add `stress-test` target to Makefile

- [ ] 7. Create chaos tests (Phase 10.4)
  - Create `tests/chaos/__init__.py` and `tests/chaos/conftest.py` (generates chaos-report.md)
  - Create `tests/chaos/test_chaos_extractor_kill.py`: kill extractor mid-pipeline, assert clear error, no partial report
  - Create `tests/chaos/test_chaos_clickhouse_port.py`: wrong port, assert actionable error within timeout
  - Create `tests/chaos/test_chaos_corrupt_video.py`: corrupt video, assert per-clip error handling, pipeline continues
  - Create `tests/chaos/test_chaos_gemini_timeout.py`: mock timeout, assert bounded retries, blast-radius containment
  - Add `chaos-test` target to Makefile

- [ ] 8. Create soak test (Phase 10.5)
  - Create `tests/soak/__init__.py` and `tests/soak/short_soak.py`
  - CLI with --minutes flag (default 30), runs pipeline --dry-run in loop
  - Log RSS memory via psutil every 60s
  - Assert final_rss <= initial_rss * 1.2
  - Generate docs/perf/soak-report.md with memory trace
  - Explicitly document scoped 30-min scope, not a production soak claim
  - Add `soak-test-short` target to Makefile

- [ ] 9. Create consolidated documentation and update PROGRESS.md
  - Create `docs/perf/README.md` linking all five reports with headline numbers
  - Update `docs/demo-video-script.md` with 15-20s beat for load-chart.png and chaos-report.md
  - Update PROGRESS.md with Phase 10.1-10.5 rows (matching existing format)
  - Do NOT modify any Phase 0-9 content

## Task Dependency Graph
```json
{
  "waves": [
    {"tasks": [1]},
    {"tasks": [2, 3, 4, 6, 7, 8]},
    {"tasks": [5]},
    {"tasks": [9]}
  ],
  "dependencies": {
    "2": ["1"],
    "3": ["1"],
    "4": ["1"],
    "5": ["3", "4"],
    "6": ["1"],
    "7": ["1"],
    "8": ["1"],
    "9": ["5", "6", "7", "8"]
  }
}
```

## Notes
- All tests assume local ClickHouse binary at .local-clickhouse/clickhouse
- Chaos tests mock/patch rather than requiring live Gemini credentials
- Soak test uses --dry-run to avoid Gemini quota burn
- matplotlib and psutil are dev-only dependencies
