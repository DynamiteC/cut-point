# Soak Test Report

**Scope**: 1-minute smoke-soak (not a production soak claim).
A true production soak test would run 12-24h under realistic load. Run the full
window with `make soak-test-short` (defaults to 30 minutes). This short run
catches the obvious leaks: unclosed MCP stdio sessions, unclosed
clickhouse-connect clients, accumulating subprocess handles. A slow leak that
only shows over hours would not appear in this window.

## Configuration
- Duration: 1 minute
- Pipeline mode: --dry-run (no Gemini/Vertex AI calls)
- Trailers cycled: demo_001, demo_002, demo_003
- Memory sample interval: 60 seconds

## Memory Trace
| Elapsed (s) | RSS (MB) | Growth from baseline |
|-------------|----------|---------------------|
| 0 | 21.9 | 0.0% |
| 59 | 22.1 | 0.9% |
| 60 | 22.1 | 0.9% |

## Result
- Baseline RSS: 21.9 MB
- Final RSS: 22.1 MB
- Growth: 0.9% over this 1-minute window
- Threshold: 20%
- **PASS** (no leak visible at this timescale; not a claim about multi-hour behavior)
