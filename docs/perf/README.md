# CutPoint Performance and Resilience Test Suite

A consolidated view of all Phase 10 test results, designed for a judge skimming in 90 seconds.

## Headline Results

| Test Type | Key Metric | Result |
|-----------|-----------|--------|
| Smoke | All 6 checks pass | PASS |
| Load (API) | p99 at 50 concurrent: 101.69ms | PASS (threshold: 2000ms) |
| Load (Ingest) | 100k batch insert: 267,891 rows/sec | Baseline established |
| Stress (API) | Ceiling found via linear ramp (see report) | Measured |
| Stress (Extractor) | Ceiling found via 3x-baseline rule (see report) | Measured |
| Chaos | 4/4 failure scenarios handled gracefully | PASS |
| Soak (30-min) | Memory growth: 0.9% | PASS (threshold: 20%) |

## Reports

- [Load Test Report](load-report.md) with [latency chart](load-chart.png)
- [Stress Test Report](stress-report.md)
- [Chaos Test Report](chaos-report.md)
- [Soak Test Report](soak-report.md)

## Honest Limitation

The soak test is a scoped 30-minute smoke-soak, not a production soak claim. A true production soak would run 12-24h under realistic load. This catches obvious leaks (unclosed connections, subprocess handle accumulation) but cannot detect slow leaks that manifest only over hours.

## Demo Video Artifact

The most impactful visuals for the demo video:
1. `load-chart.png`: latency stays flat even at 100 concurrent (proves system handles load)
2. `chaos-report.md` table: 4/4 PASS (proves graceful degradation)
