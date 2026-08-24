# CutPoint

A deterministic multi-step AI agent that fuses per-second trailer audience-retention analytics
(ClickHouse) with Gemini's frame-level video understanding to tell a studio marketing team
exactly WHERE a trailer loses viewers and WHY, then emits timestamped "Director's Notes" with
recut recommendations.

The loop: ClickHouse answers WHERE (second 47, cohort 18-24, 22% retention cliff), Gemini
multimodal answers WHY (tone shift / spoiler / pacing collapse in those exact frames), the
agent renders Director's Notes + a recut plan.

Built for the Agentic Cinema hackathon (Google Cloud, ClickHouse track).

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

Five commands from clone to a rendered report (assumes ClickHouse and GCP credentials in `.env`):

```bash
uv sync
make schema
make generate-data load
make smoke                       # pre-commit sanity check (under 60s)
make demo                        # generates the report at data/reports/demo_001.html
```

Run `make preflight` at any time to see which prerequisites are missing and how to fix each one.

Run `make smoke` before every push as a fast sanity gate.

---

## High-Level Design and Architecture

### System Context

CutPoint operates as a backend analytics pipeline. A frontend (built separately via Replit Agent,
see `docs/frontend-spec.md`) calls the REST facade. The pipeline runs synchronously per request.

```
User -> Replit Frontend -> CutPoint REST API (FastAPI)
                                |
                                v
                     CutPoint Agent (Google ADK SequentialAgent)
                     ├── [1] Analyst: ClickHouse via mcp-clickhouse (read-only)
                     ├── [2] Extractor: ffmpeg via segment extractor service
                     ├── [3] Diagnostician: Gemini via Vertex AI
                     └── [4] Reporter: Pydantic -> JSON/MD/HTML

External dependencies:
  - ClickHouse Cloud (or local binary): per-second retention analytics
  - Vertex AI Gemini: multimodal video understanding
  - ffmpeg: frame-accurate clip extraction
```

### Design Principles

1. **Determinism over autonomy**: The pipeline order is fixed by code (`SequentialAgent`),
   never chosen by an LLM. The model is used only for perception and language.

2. **Read-only analytics access**: All agent-side ClickHouse queries go through `mcp-clickhouse`
   (read-only by construction). The write path (`ingest/`, `make schema`) uses
   `clickhouse-connect` directly and is structurally separated.

3. **Fail loud, never corrupt**: Every failure surfaces as a specific, actionable error.
   No silent stubs, no partial writes to `data/reports/`. Proven by chaos tests (Phase 10.4).

4. **Independently testable steps**: Each pipeline step is a pure function wrapped in an ADK
   `BaseAgent`. Tests mock at the function boundary, not the agent boundary.

5. **Cost-aware by default**: Gemini is called only for perception (per-cliff clip diagnosis).
   Load tests cap `/analyze` at concurrency 3 to avoid quota burn.

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  CutPoint Agent (Google ADK, Python)                            │
│  SequentialAgent: deterministic 4-step pipeline                 │
│                                                                 │
│  ┌─────────────┐    ┌────────────┐    ┌──────────────┐         │
│  │  [1] analyst │ -> │[2] extractor│ -> │[3] diagnosti-│         │
│  │  LlmAgent   │    │ BaseAgent  │    │  cian        │         │
│  │  mcp-CH     │    │ HTTP+ffmpeg│    │  Gemini      │         │
│  └──────┬──────┘    └─────┬──────┘    └──────┬───────┘         │
│         │                  │                  │                 │
│         v                  v                  v                 │
│                     ┌────────────┐                              │
│                     │[4] reporter│ -> Director's Notes           │
│                     │ BaseAgent  │    (JSON / MD / HTML)         │
│                     └────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
         │                    │                   │
         v                    v                   v
  ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐
  │ClickHouse    │   │ Segment       │   │ Vertex AI        │
  │(mcp-clickhouse│   │ Extractor     │   │ Gemini           │
  │ read-only)   │   │ (FastAPI+ffmpeg)│   │ (multimodal)     │
  └──────────────┘   └───────────────┘   └──────────────────┘
```

### Data Flow (single /analyze request)

1. Frontend calls `POST /analyze {trailer_id}`
2. API facade invokes `agent/run_pipeline.py`
3. **Analyst**: `LlmAgent` with `mcp-clickhouse` toolset executes 4 SQL templates:
   - `retention_curve.sql`: per-second, per-cohort normalized retention
   - `changepoints.sql`: MAD-based z-score cliff detection (z > 3, drop >= 3%)
   - `cohort_divergence.sql`: surface demographic-specific cliffs
   - `milestone_funnel.sql`: 25/50/75/complete via `windowFunnel()`
4. **Extractor**: For each cliff in `AnalysisResult`, clips [second-5, second+5] via HTTP
5. **Diagnostician**: Sends each clip to Gemini with structured prompt, gets `Diagnosis`
6. **Reporter**: Merges into `DirectorsNotes`, writes JSON/MD/HTML to `data/reports/`
7. API returns `{"report_id": trailer_id}`

---

## Low-Level Design

### Module Decomposition

```
agent/
├── cutpoint_agent/
│   ├── agent.py           # root_agent: SequentialAgent with 4 sub-agents
│   ├── schemas.py         # Pydantic models (AnalysisResult, Diagnosis, DirectorsNotes)
│   ├── mcp.py             # mcp-clickhouse stdio session management
│   └── steps/
│       ├── analyst.py     # LlmAgent + McpToolset (the only LLM-driven step)
│       ├── extractor.py   # BaseAgent wrapping run_extraction()
│       ├── diagnostician.py  # BaseAgent wrapping diagnose_clip() per cliff
│       └── reporter.py    # BaseAgent wrapping build_directors_notes()
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
├── template.md.j2
├── template.html.j2

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
| `api/` | Thin REST facade (FastAPI) |
| `ingest/` | Synthetic data generator + ClickHouse loader (the only write path) |
| `services/segment_extractor/` | FastAPI + ffmpeg clip extraction service |
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
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | GCP project and region |
| `GEMINI_MODEL` | Model id, validated by `make preflight` |
| `SEGMENT_EXTRACTOR_URL` | Extractor service URL (local or Cloud Run) |
| `GCS_BUCKET` | Optional: blank = clips stored locally |
| `API_PORT` | Port for the REST facade |

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
| **Load** | p99 API latency 102ms at 50 concurrent, 268k rows/sec ingest | `make load-test` |
| **Stress** | Concurrency ceiling measured before degradation | `make stress-test` |
| **Chaos** | 4/4 failure scenarios: fails loud, never corrupts | `make chaos-test` |
| **Soak** | <1% memory growth over 30 min (threshold 20%) | `make soak-test-short` |

Full reports with charts: [docs/perf/README.md](docs/perf/README.md)

---

## Development Notes

- **Pre-commit check**: Run `make smoke` before every push. It starts ClickHouse, verifies
  all services respond, and runs the pipeline in dry-run mode in under 60 seconds.
- **Testing**: `uv run pytest -v` runs the full suite (36 tests). Chaos tests validate
  failure modes. Load/stress/soak run as standalone scripts via Makefile targets.
- **No frontend in this repo**: `docs/frontend-spec.md` is a complete build prompt for
  Replit Agent. The REST facade at `api/main.py` provides the stable contract.
- **Read-only agent access**: All agent ClickHouse queries go through `mcp-clickhouse`.
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
| [docs/architecture.md](docs/architecture.md) | Full system architecture and multi-agent pipeline flow |
| [docs/DEVPOST_SUBMISSION.md](docs/DEVPOST_SUBMISSION.md) | Devpost submission overview and hackathon pitch |
| [docs/demo-video-script.md](docs/demo-video-script.md) | 3-minute demo video script and walkthrough |
| [docs/frontend-spec.md](docs/frontend-spec.md) | UI design specification and component breakdown |
| [docs/perf/README.md](docs/perf/README.md) | Performance, soak, and resilience test suite overview |
| [docs/perf/load-report.md](docs/perf/load-report.md) | ClickHouse & API load test benchmarks with charts |
| [docs/perf/stress-report.md](docs/perf/stress-report.md) | Concurrency breaking point analysis |
| [docs/perf/chaos-report.md](docs/perf/chaos-report.md) | Network partition & failure scenario matrix |
| [docs/perf/soak-report.md](docs/perf/soak-report.md) | Memory stability and resource leak trace |

---

## License

MIT. See [LICENSE](LICENSE).
