# CutPoint Blockers

This file tracks phases that cannot be gated to green on the current build machine because
required external infrastructure or credentials are absent, per TASK.md rule 3 (3-iteration cap,
then document and continue with independent phases).

## ClickHouse Cloud (resolved with a local stand-in)

No ClickHouse Cloud credentials and no running Docker daemon were available on this build
machine, so instead of blocking phases 1-3, a local standalone ClickHouse binary was downloaded
(official installer, no sudo required) and run as a local server at `.local-clickhouse/clickhouse
server`, listening on `localhost:8123` (HTTP) and `localhost:9000` (native). `.env` points at
this local instance with `CLICKHOUSE_SECURE=false`. The schema, SQL analysis templates, and
`mcp-clickhouse` wiring are identical whether the host is ClickHouse Cloud or a local server --
only the `.env` values differ. This means phases 1, 2, and 3 are NOT blocked; they run against
this local ClickHouse. `.env.example` remains shaped for ClickHouse Cloud
(`CLICKHOUSE_SECURE=true`, port 8443) since that's the target deployment; `.env` (gitignored)
uses the local override for this environment only.

To point at real ClickHouse Cloud instead: stop the local server, set `CLICKHOUSE_HOST` to the
Cloud hostname, `CLICKHOUSE_PORT=8443`, `CLICKHOUSE_SECURE=true`, `CLICKHOUSE_VERIFY=true`, and
`CLICKHOUSE_PASSWORD` in `.env`, then re-run `make schema`, `make generate-data load`,
`make verify-data`, `make mcp-smoke`.

Phase 7 (`make demo`) still requires live Gemini/Vertex AI (see below) even though ClickHouse
itself is available locally.

Verified: `make demo` runs data generation, starts the segment extractor service, opens a real
MCP session, and reaches the analyst agent's first live model call, which fails with a clean
`MissingCredentialError` pointing at `gcloud auth application-default login` and
`GOOGLE_CLOUD_PROJECT` -- confirming every other part of the pipeline (session state wiring,
mcp-clickhouse stdio connection, extractor service startup) works and the ONLY blocker is
Vertex AI credentials.

## Live Vertex AI / Gemini

Missing: `gcloud` CLI is not installed, so there is no Application Default Credentials (ADC), no
GCP project is configured, and Vertex AI cannot be reached.

Affected phases:
- Phase 7 (end-to-end demo): the diagnostician step needs a live Gemini call via Vertex AI for
  each cliff clip.
- Phase 0 preflight `vertex:model-available` check reports FAIL (expected, non-fatal in
  `--report-only` mode).

Unblock: install the Google Cloud SDK, run `gcloud auth application-default login`, set
`GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` in `.env`, enable the Vertex AI API on that
project, then re-run `make preflight` and `make demo`.

## What IS fully working on this machine

Phases 0, 1, 2, 3 (all against the local ClickHouse stand-in described above), 4 (segment
extractor, real ffmpeg/ffprobe present), 5 (agent core, mocked Gemini + mocked extractor per
TASK.md's own spec), 6 (report renderer, golden-file fixture), 8 (API facade with pipeline
mocked), and 9 (docs + verify-all excluding the live-Vertex preflight check) are fully
implemented and gated green on this machine. Only the Gemini/Vertex AI call inside Phase 7's
diagnostician step is genuinely blocked by missing GCP credentials.
