# Known Limitations

This file previously listed Vertex AI as blocked on missing credentials. That is no longer
true and has not been since 24 August 2026: live Gemini output is in `data/reports/demo_001.json`
and the full pipeline runs end to end. What follows is the honest current list.

## Synthetic data

`ingest/generate.py` produces synthetic playback events with deliberately injected cliffs, and
`data/ground_truth.json` records where they were injected. This is what makes detection
accuracy measurable, but it is not real audience telemetry. `demo_control` is a trailer with no
injected cliff, and the detector correctly returns zero for it, which is the non-circular check
that the detector is not simply finding whatever it was told to find.

The sample footage is a Creative Commons animated short, not a real trailer, so Gemini's
diagnoses describe genuine on-screen content but the narrative stakes are lower than a real
marketing cut would carry.

## The analyst step is unreliable, by observation

The `LlmAgent` transcribing tool output into a schema is the weakest link, and it fails in two
ways we have seen repeatedly: it pads structured output with whitespace and truncates mid-JSON,
and it reports cliffs that are not in the database while missing ones that are. Both are
contained rather than solved: the validator re-derives every number from ClickHouse, and the
analyst is wrapped so its failure writes an empty result instead of ending the run. If you want
the pipeline's numbers, they come from step 2, never step 1.

## ClickHouse must be reachable from Cloud Run

The deployed services cannot reach a ClickHouse running on a developer's machine.
`deploy/deploy_all.sh` refuses to bake a localhost host into a Cloud Run service unless
`CUTPOINT_ALLOW_LOCAL_CH=1` is set. A full cloud demonstration needs ClickHouse Cloud or another
network-reachable instance; the local path is complete and is what `make demo` exercises.

## The scheduled loop is paused by default

An enabled `*/15` tick wakes the watcher 96 times a day indefinitely. The scheduler is created
paused and must be resumed deliberately for a live demonstration. See the cost notes in
`deploy/deploy_all.sh`.

## TLS to ClickHouse Cloud is untested

`CLICKHOUSE_SECURE=true` with certificate verification has never been exercised against a real
ClickHouse Cloud endpoint. The code path exists and is the documented default in
`.env.example`, but it has only ever run against a local plaintext server.

## Coverage

There is no coverage gate. 74 tests cover the analysis SQL, the agent steps, the validator, the
watcher, the API auth surface, report rendering and escaping, ingest checkpointing, and four
chaos scenarios, but no percentage is enforced in CI. There is no CI.
