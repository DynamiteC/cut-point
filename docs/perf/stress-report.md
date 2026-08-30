# Stress Test Report

## Results

Approximate ceilings from a single local run; treat them as indicative, not
exact. Regenerate with `make stress-test`, which prints the per-step ramp.

| Metric | Value (approximate) |
|--------|-------|
| API (GET /report) ceiling | around 200 concurrent requests |
| Segment extractor ceiling | around 8 concurrent extractions |

## Methodology
- API: linear ramp +10 concurrent per step, stop on >5% errors or p99 >10s
- Extractor: linear ramp +2 concurrent per step, stop on latency >3x baseline
- The last passing step is reported as the ceiling; the raw per-step table is
  produced by the tool at run time and is not committed here.

## Notes
- Results measured against local standalone ClickHouse binary and local uvicorn servers
- Extractor ceiling is CPU-bound (ffmpeg re-encode per clip)
- Regenerate these numbers with: `make stress-test`
