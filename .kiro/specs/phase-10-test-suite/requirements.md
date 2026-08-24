# Requirements: Phase 10 Test Suite

## Functional Requirements

### FR-1: Smoke Test (Phase 10.1)
A `scripts/smoke.sh` script performs the minimal "is the system alive" check in under 60 seconds:
- ClickHouse reachable via SELECT 1
- mcp-clickhouse spawns and lists tools
- Segment extractor /health returns 200
- API /trailers returns the demo trailers
- One full `run_pipeline.py --trailer demo_001 --dry-run` completes successfully (no cloud calls)
- `make smoke` target exits 0

### FR-2: Load Test (Phase 10.2)
- `tests/load/ingest_load.py`: pushes events at 10k/50k/100k rows per batch, measures clickhouse-connect insert throughput (rows/sec) and p50/p95/p99 insert latency per batch
- Load-tests retention_curve.sql and changepoints.sql concurrently for 3 trailers x 20 iterations, captures p50/p95/p99 query latency
- `tests/load/api_load.py`: async client (httpx.AsyncClient) hits GET /trailers and GET /report/{id} at 10/50/100 concurrent requests, captures p50/p95/p99 and error rate
- POST /analyze capped at concurrency 3 max (Gemini quota constraint, commented)
- `scripts/load_report.py` renders docs/perf/load-report.md with latency-vs-concurrency chart (matplotlib, saved as docs/perf/load-chart.png)
- `make load-test` produces load-chart.png and asserts p99 API latency under 2s at 50 concurrent /trailers requests

### FR-3: Stress Test (Phase 10.3)
- `tests/stress/find_breaking_point.py`: ramps concurrency on GET /report/{id} until error rate exceeds 5% or p99 exceeds 10s
- Also stresses segment extractor with overlapping /extract requests until latency triples
- Reports concurrency ceiling
- `make stress-test` produces docs/perf/stress-report.md

### FR-4: Chaos Test (Phase 10.4)
Four pytest scenarios in `tests/chaos/`:
1. Kill extractor mid-pipeline: assert clear error, no partial report file
2. Wrong ClickHouse port: assert specific actionable error within timeout, not a hang
3. Corrupt video file: assert ffmpeg failure caught per-clip, pipeline continues for other clips
4. Gemini API timeout mock: assert retries with backoff+jitter, bounded retries, blast-radius containment
- Output: docs/perf/chaos-report.md with scenario table
- `make chaos-test`: all 4 PASS

### FR-5: Soak Test (Phase 10.5)
- `tests/soak/short_soak.py`: runs E2E pipeline loop for 30 min (or --minutes flag), logs RSS memory every 60s
- Asserts memory does not grow more than 20% over the run
- `make soak-test-short` gate
- Document explicitly this is a scoped 30-min smoke-soak, not a production soak

### FR-6: Consolidated Documentation
- `docs/perf/README.md` links all five reports with headline numbers
- Updated `docs/demo-video-script.md` with 15-20s beat showing load-chart.png and chaos-report.md
- Updated PROGRESS.md with Phase 10.1-10.5 rows

## Non-Functional Requirements

### NFR-1: Additive Only
Never modify any Phase 0-9 file. Only add to tests/, scripts/, docs/.

### NFR-2: Dev Dependency
matplotlib added as a dev dependency only (in pyproject.toml [dependency-groups] dev).

### NFR-3: Self-Contained
smoke.sh starts and stops local ClickHouse itself. Load/stress tests generate their own fixture data.

### NFR-4: Hackathon Scope
Favor breadth over depth. Each test type produces ONE artifact usable in a 3-minute demo video.

### NFR-5: Cost Awareness
If ClickHouse Cloud costs are a concern from load/stress volume, note in load-report.md. Do not silently lower thresholds.
