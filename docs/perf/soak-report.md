# Soak Test Report

**Scope**: 30-minute smoke-soak (not a production soak claim).
A true production soak test would run 12-24h under realistic load.
This scoped test catches the obvious leaks: unclosed MCP stdio sessions,
unclosed clickhouse-connect clients, accumulating subprocess handles.

## Configuration
- Duration: 1 minutes
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
- Growth: 0.9%
- Threshold: 20%
- **PASS**
