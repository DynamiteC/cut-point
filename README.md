# CutPoint

A deterministic multi-step AI agent that fuses per-second trailer audience-retention analytics
(ClickHouse) with Gemini's frame-level video understanding to tell a studio marketing team
exactly WHERE a trailer loses viewers and WHY, then emits timestamped "Director's Notes" with
recut recommendations.

The loop: ClickHouse answers WHERE (second 48, cohorts 18-34, 13.5% retention cliff), Gemini
multimodal answers WHY (tone shift / spoiler / pacing collapse in those exact frames), the
agent renders Director's Notes + a recut plan.

It does not wait to be asked. Cloud Scheduler ticks a Pub/Sub topic, a watcher re-runs cliff
detection over live data, and a genuinely new cliff triggers the whole diagnosis pipeline with
no human in the loop.

**Runs on Google Cloud.** Cloud Run (three services), Pub/Sub (two topics with OIDC push
subscriptions), Firestore, Cloud Scheduler, GCS, and Gemini 3.5 Flash on Vertex AI. The
deployment is cost-managed: the scheduler is paused and the paid endpoints require an
allowlisted OIDC caller (see "A note on the running deployment").

| | |
|---|---|
| Model | `gemini-3.5-flash` via Vertex AI (`GOOGLE_CLOUD_LOCATION=global`) |
| Agent framework | Google ADK 2.x (`SequentialAgent`, `LlmAgent`, `BaseAgent`, `McpToolset`) |
| Google Cloud infrastructure | **Cloud Run, Pub/Sub, Firestore** (plus Cloud Scheduler and GCS) |
| Analytics | ClickHouse (`AggregatingMergeTree`, `uniqState`/`uniqMerge`, `windowFunnel`) |

All code in this repository was written between 19 and 31 August 2026. The same codebase is
entered in the All Things Agentic Hackathon and in Agentic Cinema (Google Cloud, ClickHouse
track); both submissions are disclosed in each.

---

## Table of Contents

1. [Quickstart](#quickstart)
2. [High-Level Design and Architecture](#high-level-design-and-architecture)
3. [Low-Level Design (LLD)](#low-level-design)
4. [Repository Layout](#repository-layout)
5. [Environment Variables](#environment-variables)
6. [Phase Gates](#phase-gates)
7. [Performance and Resilience](#performance-and-resilience)
8. [Development Notes](#development-notes)
9. [Documentation Index](#documentation-index)
10. [License](#license)

---

## Quickstart

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on PATH (`brew install ffmpeg`)
- A Google Cloud project with the Vertex AI API enabled, and `gcloud auth application-default login`
- ClickHouse: nothing to install. `make ch-up` runs a local server from `.local-clickhouse/`.

### Run it locally, end to end

```bash
git clone https://github.com/DynamiteC/cut-point && cd cut-point
cp .env.example .env                 # then set GOOGLE_CLOUD_PROJECT
uv sync

make ch-up                           # start the local ClickHouse server
make schema                          # tables + materialized views (idempotent)
make generate-data load              # synthetic events with injected ground-truth cliffs
make verify-data                     # confirm the load

make preflight                       # every prerequisite, with a fix line for each failure
make demo                            # full pipeline -> data/reports/demo_001.{json,md,html}
```

`make preflight` is the one to run when something is wrong: it checks binaries, every required
environment variable, ClickHouse reachability, Google ADC, and that `GEMINI_MODEL` is genuinely
servable from your configured location by making a real call.

### Deploy it to Google Cloud

```bash
gcloud auth login && gcloud config set project YOUR_PROJECT
# CLICKHOUSE_HOST must be reachable from Cloud Run (ClickHouse Cloud), not localhost

./services/segment_extractor/deploy_cloud_run.sh   # private ffmpeg service
./deploy/deploy_all.sh --dry-run                   # print every command, change nothing
./deploy/deploy_all.sh                             # API + watcher + Pub/Sub + Firestore + Scheduler
```

`deploy_all.sh` is idempotent and safe to re-run. It creates service accounts, Firestore, a GCS
bucket, both topics with OIDC push subscriptions, the Cloud Scheduler tick, and the `cutpoint-api`
and `cutpoint-watcher` services, each pinned to `--min-instances=0 --max-instances=1`. It does
**not** deploy the extractor: that has its own script, run first above, and `deploy_all.sh` only
resolves its URL into `SEGMENT_EXTRACTOR_URL`.

**The Cloud Scheduler job is created paused.** After a plain deploy the loop does not tick until
you resume it, or deploy with `CUTPOINT_ENABLE_SCHEDULE=1`.

**Run `./deploy/teardown.sh` after judging** to stop all charges. Add `--purge-data` to also
remove Firestore contents and the bucket.

### The UI

`app.html` reads live from the deployed API and needs no build step. Open it locally with any
static server, or point it at another API with `?api=`:

```bash
python -m http.server 8899          # then open http://127.0.0.1:8899/app.html
```

Published at https://dynamitec.github.io/cut-point/app.html once the repository is pushed with GitHub Pages enabled.

It is a reader, not a trigger. `POST /analyze` requires an allowlisted service account, so the
page explains that 401 rather than offering a button that would always fail.

### A note on the running deployment

The services can be deployed from this repo and the read endpoints are reachable when the
deployment is up, but it is deliberately cost-managed rather than left running hot:

- **Cloud Scheduler is paused.** An enabled `*/15` tick wakes the watcher 96 times a day
  indefinitely. Resume it to watch the loop run, then pause it again.
- **The paid endpoints return 401 by design.** Each call runs ClickHouse queries, an ffmpeg
  extraction per cliff and a Gemini video inference per clip, so they require a verified Google
  OIDC token from an allowlisted service account rather than being open to the internet.
- **There is a daily ceiling** of 25 analyses and a concurrency cap of 2.

The All Things Agentic rules state an application "does not need to be publicly accessible or
deployed at the exact moment of submission or judging" provided deployment is demonstrated. The
demo video shows the full loop executing, alongside the Cloud Run dashboard, the Pub/Sub
subscriptions and Vertex AI logs. `GET /report/demo_001` returns a real report when the service
is running.

### Verify the autonomous loop

```bash
gcloud pubsub topics publish cutpoint-retention-scan --message '{}'
gcloud run services logs read cutpoint-watcher --region us-central1 --limit 20
```

The watcher logs the fingerprint diff and, when a genuinely new cliff appears, publishes to
`cutpoint-analyze`. The API's push handler then runs the full pipeline with no human involved.

---

## High-Level Design and Architecture

### System Context

CutPoint runs as an event-driven service on Google Cloud. Nothing needs to ask it for a report.

```
Cloud Scheduler  (*/15 * * * *)
      |
      v
Pub/Sub  cutpoint-retention-scan
      |  (push, OIDC)
      v
Cloud Run  cutpoint-watcher
      |  re-runs changepoints.sql, fingerprints the cliff set,
      |  compares against Firestore, publishes ONLY on a real change
      v
Pub/Sub  cutpoint-analyze
      |  (push, OIDC, ack-deadline 600s)
      v
Cloud Run  cutpoint-api  ->  ADK SequentialAgent (5 steps)
      |                        [1] analyst        reads ClickHouse directly, readonly=1
      |                        [2] extractor      -> Cloud Run cutpoint-segment-extractor
      |                        [3] diagnostician  -> Vertex AI gemini-3.5-flash (vision)
      |                        [4] narrator       -> Vertex AI + mcp-clickhouse (language)
      |                        [5] reporter       -> Director's Notes
      v
Firestore (reports, jobs, watch fingerprints)  +  GCS (clips, rendered HTML)
      ^
      |  GET /report/{id}, GET /jobs/{id}   (public, read-only)
GitHub Pages UI
```

External dependencies:
  - ClickHouse Cloud (or a local server via `make ch-up`): per-second retention analytics
  - Vertex AI Gemini 3.5 Flash: multimodal video understanding
  - ffmpeg: frame-accurate clip extraction

### Why no model touches a number

The analyst used to be an `LlmAgent` that queried ClickHouse through
`mcp-clickhouse` and transcribed the results into a Pydantic schema. `output_schema`
validates the shape of a transcription, not its numbers, so a transposed digit, a
missed cliff or an invented one passed silently.

We measured it. On a real run it reported one cliff, at second 2, which does not
exist in the database, and missed all three that do:

```
llm cliffs claimed : 1
db-verified cliffs : 3
second 48 / 23 / 69 : missed by the analyst, restored from ClickHouse
second 2            : reported by the analyst, absent from ClickHouse
```

So the numbers were moved off it entirely. Step 1 now reads ClickHouse directly
over a `readonly=1` connection, and no model sits between the database and the
report. The comparison above is preserved in `validator.validate()` and its
tests, because it is the reason the pipeline looks like this.

That buys provenance and reproducibility, not correctness: a wrong query is
re-derived wrongly every time. Detector accuracy is a separate claim, evidenced
by `tests/test_detector.py` against injected ground truth with `demo_control` as
a non-circular false-positive control.

### What the model is for

Steps 3 and 4, and neither can put a number in the report.

Step 3 looks at frames and says what is on screen. Step 4 turns verified findings
into a paragraph an editor can act on, which is a job a format string does badly
and a model does well. `mcp-clickhouse` is available to it for supporting context.

Step 4 also demonstrates the failure mode in miniature. Its first version named
the session-state keys in the instruction instead of interpolating them, so ADK
passed no data and the model confidently described a "poorly rendered CGI
explosion" and "3,525 viewers", neither of which appears anywhere in the
diagnoses. The prompt now interpolates the real data, and
`summary_is_grounded()` rejects any summary citing a second that was not
detected as a cliff, falling back to the deterministic template.

### Security model

| Endpoint | Access |
|---|---|
| `GET /trailers`, `/report/{id}`, `/report/{id}/html`, `/jobs/{id}`, `/health` | public, read-only |
| `POST /analyze`, `POST /jobs`, `POST /pubsub/analyze` | verified Google OIDC, audience pinned to the service URL, caller pinned to an allowlist |
| `cutpoint-watcher`, `cutpoint-segment-extractor` | private, platform-level `--no-allow-unauthenticated` |

Each paid request runs ClickHouse queries, an ffmpeg extraction per cliff and a Gemini video
inference per clip, so the write path is authenticated, capped for concurrency and sheds with
429. `GET /jobs/{id}` returns an allowlist of progress fields only: raw exception text and
caller identity are never served anonymously.

### Design Principles

1. **Determinism over autonomy**: The pipeline order is fixed by code (`SequentialAgent`),
   never chosen by an LLM. The model is used only for perception and language.

2. **Read-only analytics access**: Every agent-side read is read-only, by two different means.
   The numeric path (analyst, and the watcher) uses `clickhouse-connect` with `readonly=1` set on
   the client by `ingest/clickhouse_client.py::get_readonly_client()`, so the server refuses a
   write. Only the narrator (step 4) reaches ClickHouse through `mcp-clickhouse`
   (read-only by construction). The write path (`ingest/`, `make schema`) uses
   `clickhouse-connect` directly and is structurally separated.

3. **Fail loud, never corrupt**: Every failure surfaces as a specific, actionable error.
   No silent stubs, no partial writes to `data/reports/`. Proven by chaos tests (Phase 10.4).

4. **Independently testable steps**: Each pipeline step is a pure function wrapped in an ADK
   `BaseAgent`. Tests mock at the function boundary, not the agent boundary.

5. **Cost-aware by default**: Gemini is called only for perception (per-cliff clip diagnosis).
   Load tests cap `/analyze` at concurrency 3 to avoid quota burn.

### Agent Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CutPoint Agent (Google ADK)                                             │
│  SequentialAgent: 5 steps, order fixed in code, never chosen by a model   │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                  │
│  │ [1] analyst  │ ->│ [2] extractor│ ->│[3] diagnostic│                  │
│  │ BaseAgent    │   │ BaseAgent    │   │ Gemini 3.5   │                  │
│  │ readonly SQL │   │ HTTP+ffmpeg  │   │ SEES frames  │                  │
│  │ NO MODEL     │   │              │   │              │                  │
│  └──────────────┘   └──────────────┘   └──────┬───────┘                  │
│                                                │                         │
│                     ┌──────────────┐   ┌───────v──────┐                  │
│  Director's Notes <-│ [5] reporter │ <-│ [4] narrator │                  │
│  JSON / MD / HTML   │ BaseAgent    │   │ Gemini 3.5   │                  │
│                     │              │   │ WRITES prose │                  │
│                     └──────────────┘   │ + mcp-CH     │                  │
│                                        └──────────────┘                  │
└──────────────────────────────────────────────────────────────────────────┘
         │                    │                       │
         v                    v                       v
  ┌───────────────┐  ┌──────────────────┐   ┌──────────────────┐
  │ ClickHouse    │  │ Cloud Run        │   │ Vertex AI        │
  │ readonly=1    │  │ segment-extractor│   │ gemini-3.5-flash │
  │ + mcp (step 4)│  │ (private)        │   │ (multimodal)     │
  └───────────────┘  └──────────────────┘   └──────────────────┘
```

Only steps 3 and 4 involve a model, and neither produces a measurement: every second, percentage
and count is read from ClickHouse in step 1. The diagnostician does return `severity` and
`confidence`, its judgement of a cliff the database found. Only `severity` reaches the report;
`confidence` is produced and then dropped, because `CliffFinding` has no field for it. Step 4's
prose is rejected
if it cites a second that was not detected as a cliff.

### Data Flow (single /analyze request)

1. Frontend calls `POST /analyze {trailer_id}`
2. API facade invokes `agent/run_pipeline.py` (or the Pub/Sub push handler does, unprompted)
3. **Analyst**: a deterministic step (no model in the numeric path) that reads ClickHouse
   directly over a `readonly=1` connection and runs the analysis SQL:
   - `retention_curve.sql`: per-second, per-cohort normalized retention
   - `changepoints.sql`: MAD-based z-score cliff detection (z > 3, drop >= 3%)
   - `cohort_divergence.sql`: surface demographic-specific cliffs
   - `milestone_funnel.sql`: 25/50/75/complete via `windowFunnel()`

   An `LlmAgent` did this job first. It reported a cliff that did not exist and missed the three
   that did, so the model was taken off the numbers entirely. Zero source rows raises rather than
   reporting "no cliffs found". The provenance is recorded in a `ValidationReport`.
4. **Validator**: the module (`validator.validate()`) that measured the old model against the
   database and is kept as the evidence for why the numeric path is model-free. Its query helpers
   (`query_cliffs`, `query_funnel`, `query_retention_end`) are what the analyst now calls directly.
5. **Extractor**: for each verified cliff, clips [second-5, second+5] via HTTP. In the cloud the
   clip is uploaded to GCS and a `gs://` URI is returned, because the diagnostician runs in a
   different container
6. **Diagnostician**: sends each clip to Gemini and gets a `Diagnosis`. The prompt is
   clip-relative, since the clip's own timeline starts at 00:00. A clip that fails is recorded
   and skipped; it does not sink the other findings
7. **Narrator**: turns the verified findings and diagnoses into an editor-facing summary via
   Gemini, with `mcp-clickhouse` available for supporting context. `summary_is_grounded()`
   rejects any summary that cites a second not detected as a cliff, falling back to the
   deterministic template
8. **Reporter**: merges into `DirectorsNotes`, writes to Firestore and GCS in the cloud, or to
   `data/reports/` locally
9. API returns `{"report_id": trailer_id}`; the Pub/Sub path updates the Firestore job to `done`

---

## Low-Level Design

### Module Decomposition

```
agent/
├── cutpoint_agent/
│   ├── agent.py           # root_agent: SequentialAgent with 5 sub-agents
│   ├── schemas.py         # Pydantic models (AnalysisResult, Diagnosis, DirectorsNotes)
│   ├── mcp.py             # mcp-clickhouse stdio session management
│   └── steps/
│       ├── analyst.py     # [1] deterministic: fixed SQL over readonly=1, no model
│       ├── extractor.py   # [2] BaseAgent wrapping run_extraction()
│       ├── diagnostician.py  # [3] BaseAgent wrapping diagnose_clip() per cliff
│       ├── narrator.py    # [4] LlmAgent + McpToolset (the only LLM-driven step)
│       └── reporter.py    # [5] BaseAgent wrapping build_directors_notes()
├── run_pipeline.py        # CLI driver (--dry-run support)

api/
├── main.py                # FastAPI: /trailers, /analyze, /report/{id}, /report/{id}/html

ingest/
├── apply_schema.py        # DDL: tables, materialized views (idempotent)
├── generate.py            # Synthetic event generator with injected ground-truth cliffs
├── load.py                # Batch loader with checkpointing
├── verify_data.py         # Post-load validation
├── clickhouse_client.py   # Client factory (ONLY place clickhouse-connect is used)

services/segment_extractor/
├── main.py                # FastAPI: /health, /extract (ffmpeg stream-copy with re-encode fallback)

report/
├── render.py              # Jinja2: JSON -> Markdown + self-contained HTML (inline SVG, no CDN)
├── templates/report.md.jinja
├── templates/report.html.jinja

sql/analysis/
├── retention_curve.sql    # Per-second per-cohort retention (normalized against baseline)
├── changepoints.sql       # MAD z-score cliff detector with cohort attribution
├── cohort_divergence.sql  # Identifies demographic-specific cliffs
├── milestone_funnel.sql   # windowFunnel() showcase (25/50/75/complete)
```

### Key Data Models (Pydantic)

```python
class AnalysisResult:
    trailer_id: str
    overall_retention_end: float
    milestone_funnel: dict[str, float]  # {"50%": 30.0, ...}
    cliffs: list[CliffPoint]            # detected retention cliffs

class CliffPoint:
    second: int              # exact second of the cliff
    drop_pct: float          # percentage drop at this second
    affected_cohorts: list[str]  # cohorts driving the drop
    z_score: float           # statistical significance

class Diagnosis:
    second: int
    on_screen: str           # what Gemini sees in the clip
    hypothesis: str          # why this caused viewer churn
    severity: int            # 1-5
    confidence: float        # 0.0-1.0

class DirectorsNotes:
    trailer_id, title, duration_s, analyzed_at
    overall_retention_end: float
    milestone_funnel: dict[str, float]
    cliffs: list[CliffFinding]  # full finding with recommendations
    executive_summary: str
```

### ClickHouse Schema

```sql
-- Raw events: one row per session per second of watch time
CREATE TABLE raw_playback_events (
    event_ts        DateTime64(3, 'UTC'),
    trailer_id      LowCardinality(String),
    session_id      UUID,
    cohort          LowCardinality(String),   -- "13-17", "18-24", "25-34", "35-44", "45+"
    region          LowCardinality(String),
    device          LowCardinality(String),
    second_offset   UInt16,
    event_type      Enum8('start'=1,'heartbeat'=2,'exit'=3,'complete'=4)
) ENGINE = MergeTree
ORDER BY (trailer_id, cohort, second_offset, event_ts)

-- Materialized view for fast per-second viewer counts
CREATE MATERIALIZED VIEW mv_second_viewers (
    trailer_id      LowCardinality(String),
    cohort          LowCardinality(String),
    second_offset   UInt16,
    viewers_state   AggregateFunction(uniq, UUID)
) ENGINE = AggregatingMergeTree
ORDER BY (trailer_id, cohort, second_offset)
AS SELECT
    trailer_id, cohort, second_offset,
    uniqState(session_id) AS viewers_state
FROM raw_playback_events
GROUP BY trailer_id, cohort, second_offset
```

### Cliff Detection Algorithm (changepoints.sql)

1. Compute per-second overall viewer count (all cohorts merged)
2. Calculate second-to-second delta
3. Compute Median Absolute Deviation (MAD) for robustness
4. Flag seconds where `|z_score| > 3` AND `drop_pct >= 3%`
5. For each flagged second, attribute cohorts whose own per-cohort drop >= 50% of the overall drop
6. Return top 10 cliffs ordered by severity

### Error Handling Strategy

| Failure Mode | Behavior | Proven By |
|-------------|----------|-----------|
| ClickHouse unreachable | Actionable error within 10s timeout | Chaos test 2 |
| Segment extractor down | Clear error naming the service, no partial report | Chaos test 1 |
| Corrupt video file | Per-clip error, pipeline continues for other clips | Chaos test 3 |
| Gemini timeout/5xx | Graceful per-cliff failure, blast-radius contained | Chaos test 4 |
| Missing credentials | `MissingCredentialError` with fix instructions | Preflight check |

### API Contract

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/trailers` | GET | - | `["demo_001", "demo_002", ...]` |
| `/analyze` | POST | `{"trailer_id": "demo_001"}` | `{"report_id": "demo_001"}` |
| `/report/{trailer_id}` | GET | - | DirectorsNotes JSON |
| `/report/{trailer_id}/html` | GET | - | Self-contained HTML report |

Security: `trailer_id` validated against `^[a-zA-Z0-9_-]{1,64}$` to prevent path traversal
and SQL injection. CORS open per hackathon spec (would need auth in production).

---

## Repository Layout

| Path | Purpose |
|------|---------|
| `agent/` | Google ADK agent: `cutpoint_agent/agent.py` is the `root_agent` entrypoint |
| `app.html` | The UI. Reads live from the deployed API; no build step |
| `agent/cutpoint_agent/steps/analyst.py` | Reads every number from ClickHouse directly. No model involved |
| `agent/cutpoint_agent/steps/narrator.py` | The model's language job, with a grounding check on its output |
| `agent/cutpoint_agent/steps/validator.py` | The measurement that moved the numbers off the model, kept as evidence |
| `agent/cutpoint_agent/store.py` | Firestore-backed reports, jobs and watch fingerprints, with a local fallback |
| `api/` | REST facade (FastAPI). `auth.py` guards the paid endpoints |
| `ingest/` | Synthetic data generator + ClickHouse loader (the only write path) |
| `services/segment_extractor/` | FastAPI + ffmpeg clip extraction service (Cloud Run, private) |
| `services/watcher/` | The autonomous trigger: scans for new cliffs and publishes work |
| `deploy/` | `deploy_all.sh` and `teardown.sh` for the whole Google Cloud footprint |
| `Dockerfile` | One image, two entrypoints (API and watcher) selected by `APP_MODULE` |
| `report/` | Jinja2 renderer: JSON to Markdown and HTML |
| `sql/` | ClickHouse schema, materialized views, 4 analysis query templates |
| `scripts/` | Preflight, smoke test, MCP smoke, demo runner, load report generator |
| `tests/` | Unit, integration, load, stress, chaos, and soak tests |
| `docs/` | Architecture, demo script, frontend spec, performance reports, progress |
| `data/` | Generated events, videos, clips, reports (gitignored except fixtures) |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in every value:

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `CLICKHOUSE_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | ClickHouse connection |
| `CLICKHOUSE_SECURE` / `CLICKHOUSE_VERIFY` | TLS settings |
| `GOOGLE_GENAI_USE_VERTEXAI` | Must be `TRUE` (Gemini via Vertex AI, never direct) |
| `GOOGLE_CLOUD_PROJECT` | GCP project id |
| `GOOGLE_CLOUD_LOCATION` | **Vertex AI** location. Must be `global`: Gemini 3.x is not served from regional endpoints and returns 404 there. `make preflight` proves this with a real call |
| `GCP_REGION` | Deploy region for Cloud Run, Pub/Sub, Scheduler. A real region, never `global` |
| `GEMINI_MODEL` | Model id, default `gemini-3.5-flash`, validated by `make preflight` |
| `SEGMENT_EXTRACTOR_URL` | Extractor service URL (local or Cloud Run) |
| `GCS_BUCKET` | Clip and media bucket. Blank = clips stay on local disk |
| `API_PORT` | Port for the REST facade |
| `CUTPOINT_STORE` | `local` (default) or `firestore`. Local keeps tests and `make demo` offline |
| `CUTPOINT_REQUIRE_AUTH` | Defaults to on. Set `false` only for local development |
| `CUTPOINT_AUDIENCE` | Pins the accepted OIDC audience to this service's own URL |
| `CUTPOINT_ALLOWED_INVOKERS` | Comma-separated service accounts permitted to call the paid endpoints |
| `CUTPOINT_ALLOWED_ORIGINS` | CORS origins for the browser UI |
| `CUTPOINT_MAX_CONCURRENT_PIPELINES` | Concurrency cap; excess requests get 429 |
| `CUTPOINT_ANALYZE_TOPIC` / `CUTPOINT_SCAN_SCHEDULE` | Pub/Sub topic and Scheduler cron |

---

## Phase Gates

Each phase has a make target that must pass before proceeding:

```bash
make preflight-report   # Phase 0:  environment check
make generate-data load && make verify-data  # Phase 1: data pipeline
make test-analysis      # Phase 2:  cliff detection accuracy
make mcp-smoke          # Phase 3:  MCP integration
make extractor-test     # Phase 4:  ffmpeg clip extraction
make test-agent         # Phase 5:  agent pipeline (mocked)
make test-report        # Phase 6:  report rendering
make demo               # Phase 7:  end-to-end (needs live credentials)
make api-test           # Phase 8:  REST facade
make verify-all         # Phase 9:  full suite (ruff + pytest + hygiene)
make smoke              # Phase 10.1: pre-commit sanity (<60s)
make load-test          # Phase 10.2: throughput + latency benchmarks
make stress-test        # Phase 10.3: find breaking point
make chaos-test         # Phase 10.4: graceful degradation (4 scenarios)
make soak-test-short    # Phase 10.5: 30-min memory leak check
```

See `docs/PROGRESS.md` for current status and `docs/BLOCKERS.md` for credential requirements.

---

## Performance and Resilience

Phase 10 delivers production-readiness evidence across five test categories:

| Test Type | Key Finding | Gate |
|-----------|-------------|------|
| **Smoke** | All system components alive in <60s | `make smoke` |
| **Load** | p99 API latency 102ms at 50 concurrent, 267,891 rows/sec ingest (local harness, single run) | `make load-test` |
| **Stress** | Concurrency ceiling measured before degradation | `make stress-test` |
| **Chaos** | 4/4 failure scenarios: fails loud, never corrupts | `make chaos-test` |
| **Soak** | <1% memory growth in the recorded 1-min run; target runs to 30 min (threshold 20%) | `make soak-test-short` |

Full reports with charts: [docs/perf/README.md](docs/perf/README.md)

---

## Development Notes

- **Pre-commit check**: Run `make smoke` before every push. It starts ClickHouse, verifies
  all services respond, and runs the pipeline in dry-run mode in under 60 seconds.
- **Testing**: `uv run pytest -v` runs the full suite (89 tests). Chaos tests validate
  failure modes. Load/stress/soak run as standalone scripts via Makefile targets.
- **Frontend**: `index.html` is the landing page and `app.html` is the working UI, both
  hosted on GitHub Pages and reading live from the deployed API. The REST facade at
  `api/main.py` provides the stable contract; `docs/frontend-spec.md` documents it.
- **Read-only agent access**: Agent reads are read-only either way. The analyst and the watcher
  use `clickhouse-connect` with `readonly=1` via `get_readonly_client()`; `mcp-clickhouse` is used
  only by the narrator, the one step where a model queries the database.
  Direct `clickhouse-connect` is confined to `ingest/` and `make schema`.
- **Mocks live only in tests**: No silent stubs. Missing credentials produce
  `MissingCredentialError` with fix instructions.
- **Local ClickHouse & Cloud Ready**: Works out of the box with a local standalone
  binary at `.local-clickhouse/clickhouse` or seamlessly connects to ClickHouse Cloud
  by updating `CLICKHOUSE_HOST` in `.env`.

---

## Documentation Index

| Document | Purpose |
|----------|---------|
| [docs/architecture.md](docs/architecture.md) | System architecture, component boundaries, and pipeline sequence flow |
| [docs/DEVPOST_SUBMISSION.md](docs/DEVPOST_SUBMISSION.md) | What was built, how, what went wrong, and what was learned |
| [docs/well-architected.md](docs/well-architected.md) | Assessment against the Google Cloud Well-Architected pillars, with the real gaps |
| [docs/BLOCKERS.md](docs/BLOCKERS.md) | Known limitations, stated plainly |
| [docs/submission-checklist.md](docs/submission-checklist.md) | Requirement to artifact traceability |
| [deploy/deploy_all.sh](deploy/deploy_all.sh) | Every Google Cloud resource this project creates, in order |
| [docs/perf/README.md](docs/perf/README.md) | Performance, soak, and resilience test suite overview |
| [docs/perf/load-report.md](docs/perf/load-report.md) | ClickHouse & REST API load test benchmarks with charts |
| [docs/perf/stress-report.md](docs/perf/stress-report.md) | Concurrency breaking point analysis |
| [docs/perf/chaos-report.md](docs/perf/chaos-report.md) | Network partition & failure scenario matrix |
| [docs/perf/soak-report.md](docs/perf/soak-report.md) | Memory stability and resource leak trace |

---

## License

MIT. See [LICENSE](LICENSE).
