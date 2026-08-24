# Chaos Test Report


| Scenario | Expected Failure Mode | Actual Behavior | Result |
|----------|----------------------|-----------------|--------|
| Wrong ClickHouse port | Actionable error within timeout | Passed as expected | PASS |
| Corrupt video file | Per-clip error, pipeline continues | Passed as expected | PASS |
| Extractor kill mid-pipeline | Clear error, no partial report | Passed as expected | PASS |
| Gemini API timeout | Bounded retries, blast-radius containment | Passed as expected | PASS |
