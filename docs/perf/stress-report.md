# Stress Test Report

## Results

| Metric | Value |
|--------|-------|
| API (GET /report) ceiling | 200 concurrent requests |
| Segment extractor ceiling | 8 concurrent extractions |

## Methodology
- API: linear ramp +10 concurrent per step, stop on >5% errors or p99 >10s
- Extractor: linear ramp +2 concurrent per step, stop on latency >3x baseline

## Notes
- Results measured against local standalone ClickHouse binary and local uvicorn servers
- Extractor ceiling is CPU-bound (ffmpeg re-encode per clip)
- Regenerate these numbers with: `make stress-test`
