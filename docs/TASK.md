# CUTPOINT — ONE-SHOT BUILD PROMPT
<!-- Paste this entire file as the first message to your coding agent (Claude Code), with cwd = root of the `cut-point` repo. -->

## 0. MISSION

You are a senior principal engineer building **CutPoint**, a hackathon-grade but production-quality prototype for the Agentic Cinema hackathon (Google Cloud, ClickHouse track).

**Product in one sentence:** a deterministic multi-step AI agent that fuses per-second trailer audience-retention analytics (ClickHouse) with Gemini's frame-level video understanding to tell a studio marketing team exactly WHERE a trailer loses viewers and WHY, then emits timestamped "Director's Notes" with recut recommendations.

**The loop:** ClickHouse answers WHERE (second 47, cohort 18–24, 22% retention cliff) → Gemini multimodal answers WHY (tone shift / spoiler / pacing collapse in those exact frames) → agent renders Director's Notes + recut plan.

You are operating in the root of the `cut-point` repository (currently empty except git metadata). Build the entire system phase by phase, verifying each phase's acceptance gate before proceeding.

---

## 1. NON-NEGOTIABLE COMPLIANCE CONSTRAINTS (HACKATHON)

Violating any of these disqualifies the submission. Treat them as invariants:

1. **Runtime LLM is Gemini via Vertex AI. Never Claude.** You (Claude) are the *builder*; the *product* must call Gemini through Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`, project + location from env). No Anthropic API calls anywhere in product code.
2. **Agent framework is Google ADK (Agent Development Kit)**, deployable to Vertex AI Agent Engine. Google Cloud usage must be real runtime usage, imported and called in code — not just named in the README.
3. **All agent-side ClickHouse access goes through the official `mcp-clickhouse` MCP server.** Direct `clickhouse-connect` is permitted ONLY in the data-loading/ingestion path (`ingest/`), never in the agent's query path. `mcp-clickhouse` is read-only by design, which enforces this split naturally.
4. **Repo hygiene for judging:** `LICENSE` file (MIT) at repo root; complete `README.md` with run instructions; `.env.example` with every required variable; no secrets ever committed.
5. **Frontend is NOT built in this repo by you.** You produce `docs/frontend-spec.md` — a complete build prompt for Replit Agent (the frontend must be built with Replit Agent and deployed on replit.app to keep the Replit track option open). You DO build a thin local REST facade so the frontend has a stable API contract.
6. **Determinism:** the agent follows a fixed 5-step diagnostic protocol. The LLM is used for perception (video diagnosis) and language (notes wording), never for deciding the pipeline order.

---

## 2. AGENTIC GROUND RULES (YOUR BEHAVIOR CONTRACT)

1. **Verify, never trust memory.** Library APIs (google-adk, google-genai, mcp-clickhouse, clickhouse-connect) drift fast. Before writing code against any of them: `pip show <pkg>`, then introspect (`python -c "import x; help(x...)"` or read installed source in site-packages). Reference snippets in this prompt are starting points — validate them against installed versions.
2. **Never fabricate model IDs.** `GEMINI_MODEL` comes from `.env`. Your `make preflight` must list available Gemini models from Vertex AI and validate the configured one exists. Default suggestion: `gemini-3-flash` (verify at preflight; prefer the newest GA flash-class multimodal model that supports video input).
3. **Phase gates are hard gates.** After each phase, run the acceptance gate command. Green → commit → next phase. Red → fix, max 3 iterations; if still red, write the failure analysis to `BLOCKERS.md`, mark the phase blocked in `PROGRESS.md`, and continue with any phase not dependent on it.
4. **Progress ledger.** Maintain `PROGRESS.md` at repo root: one line per phase — `Phase N | status (done/blocked/pending) | gate command | last result | commit hash`. Update it before and after every phase.
5. **Resume protocol.** If this session is a continuation, FIRST read `PROGRESS.md` and `BLOCKERS.md`, then resume from the first non-done phase. Do not redo completed phases.
6. **Commits:** one commit per phase minimum, conventional format (`feat(phase-2): analysis SQL library + changepoint detector`). Do not push unless asked.
7. **No decorative stubs.** Every function on the critical demo path must work. Where cloud creds are absent, degrade with an explicit, actionable error (`MissingCredentialError: set CLICKHOUSE_HOST in .env — see README §Setup`), never a silent mock. Mocks live only in tests.
8. **Idempotency.** Every script (schema apply, data load, deploy) must be safely re-runnable: `CREATE TABLE IF NOT EXISTS`, truncate-then-load guarded by a `--force` flag, checkpointed batch loads.
9. **Secrets discipline:** `.env` in `.gitignore` from Phase 0. Grep for credential patterns before every commit. Log statements never print env values.
10. **Local-first, cloud-real.** Everything runs from a laptop against ClickHouse Cloud + Vertex AI using `.env` creds. Cloud Run / Agent Engine deployment is scripted but optional to execute.
11. **Ask nothing.** If ambiguity arises, state the assumption in a code comment and in `PROGRESS.md`, choose the battle-tested option, and proceed.

---

## 3. TARGET ARCHITECTURE

```
                        ┌─────────────────────────────────────────────┐
                        │  CutPoint Agent (Google ADK, Python)        │
                        │  SequentialAgent — deterministic pipeline   │
  Replit frontend  ───► │                                             │
  (built separately     │  [1] analyst      ──McpToolset──► mcp-clickhouse ──► ClickHouse Cloud
   from frontend-spec)  │      retention curve + changepoints          │        (raw events, MVs)
        │               │  [2] extractor    ──HTTP────────► segment service (FastAPI+ffmpeg,
        ▼               │      clip ±5s around each cliff              │         local or Cloud Run)
  api/ REST facade ───► │  [3] diagnostician ─Vertex AI──► Gemini multimodal (video Part)
  (FastAPI, thin)       │      "what happens on screen & why they leave"
                        │  [4] reporter     ──renders────► Director's Notes (JSON→MD→HTML)
                        └─────────────────────────────────────────────┘
  ingest/ (write path, clickhouse-connect ONLY here):
    synthetic event generator (+ ground truth) ──► loader ──► ClickHouse Cloud
```

**Repo layout (create in Phase 0):**

```
cut-point/
├── LICENSE                  # MIT
├── README.md
├── PROGRESS.md
├── Makefile
├── pyproject.toml           # uv-managed, python >=3.12
├── .env.example
├── .gitignore
├── sql/
│   ├── 001_schema.sql
│   ├── 002_materialized_views.sql
│   └── analysis/            # named .sql templates used via MCP
├── ingest/
│   ├── generate.py          # synthetic events + ground_truth.json
│   └── load.py              # clickhouse-connect batch loader
├── agent/
│   ├── cutpoint_agent/
│   │   ├── __init__.py
│   │   ├── agent.py         # root SequentialAgent (ADK entrypoint: root_agent)
│   │   ├── mcp.py           # McpToolset factory for mcp-clickhouse
│   │   ├── steps/           # analyst.py, extractor.py, diagnostician.py, reporter.py
│   │   ├── schemas.py       # pydantic models incl. DirectorsNotes
│   │   └── prompts.py
│   ├── run_pipeline.py      # CLI driver: python -m agent.run_pipeline --trailer demo_001
│   └── deploy_agent_engine.py
├── services/segment_extractor/
│   ├── main.py              # FastAPI /health /extract
│   ├── Dockerfile
│   └── deploy_cloud_run.sh
├── api/
│   └── main.py              # REST facade for frontend: POST /analyze, GET /report/{id}
├── report/
│   ├── render.py            # JSON → markdown + HTML (Jinja2 timeline)
│   └── templates/
├── scripts/
│   ├── preflight.py
│   ├── mcp_smoke.py
│   └── fetch_sample_video.sh
├── tests/
├── data/                    # gitignored: generated events, ground_truth.json, clips, reports
└── docs/
    ├── architecture.md      # with mermaid diagram
    ├── frontend-spec.md     # Replit Agent build prompt
    ├── demo-video-script.md
    └── submission-checklist.md
```

---

## 4. ENVIRONMENT CONTRACT

Assume the human has: Python 3.12+, `uv`, `ffmpeg`+`ffprobe`, `gcloud` CLI authenticated (`gcloud auth application-default login`), a ClickHouse Cloud service, a GCP project with Vertex AI API enabled. `make preflight` verifies ALL of this and fails with per-item fix instructions.

`.env.example` (exact keys):

```
# ClickHouse Cloud
CLICKHOUSE_HOST=
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DATABASE=cutpoint
CLICKHOUSE_SECURE=true
CLICKHOUSE_VERIFY=true

# Google Cloud / Vertex AI
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-3-flash          # validated by make preflight

# Services
SEGMENT_EXTRACTOR_URL=http://localhost:8081
GCS_BUCKET=                          # optional; blank = local clip storage
API_PORT=8080
```

Core deps (pyproject): `google-adk`, `google-cloud-aiplatform[adk,agent_engines]>=1.101.0`, `google-genai`, `clickhouse-connect`, `fastapi`, `uvicorn`, `httpx`, `pydantic>=2`, `python-dotenv`, `jinja2`, `numpy`, `pytest`, `pytest-asyncio`, `ruff`.

---

## 5. DATA MODEL SPEC (ClickHouse)

Design axioms: ORDER BY matches the dominant query pattern (per-trailer, per-cohort, per-second aggregation); no high-cardinality columns in ORDER BY (session_id stays OUT — it would destroy merge performance); LowCardinality for enum-like strings; partition by month.

**`sql/001_schema.sql`** — target design (finalize syntax against ClickHouse Cloud):

```sql
CREATE DATABASE IF NOT EXISTS cutpoint;

CREATE TABLE IF NOT EXISTS cutpoint.raw_playback_events
(
    event_ts        DateTime64(3, 'UTC'),
    event_date      Date DEFAULT toDate(event_ts),
    trailer_id      LowCardinality(String),
    session_id      UUID,
    cohort          LowCardinality(String),   -- '13-17','18-24','25-34','35-44','45+'
    region          LowCardinality(String),
    device          LowCardinality(String),   -- 'mobile','desktop','tv'
    second_offset   UInt16,                    -- playback position, 1 heartbeat/sec/session
    event_type      Enum8('start'=1,'heartbeat'=2,'exit'=3,'complete'=4)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (trailer_id, cohort, second_offset, event_ts);

CREATE TABLE IF NOT EXISTS cutpoint.trailers
(
    trailer_id      LowCardinality(String),
    title           String,
    duration_s      UInt16,
    video_path      String,                    -- local path or gs:// URI
    created_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY trailer_id;
```

**`sql/002_materialized_views.sql`** — pre-aggregated per-second unique viewers:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS cutpoint.mv_second_viewers
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (trailer_id, cohort, second_offset)
AS SELECT
    event_date, trailer_id, cohort, second_offset,
    uniqState(session_id) AS viewers_state
FROM cutpoint.raw_playback_events
WHERE event_type = 'heartbeat'
GROUP BY event_date, trailer_id, cohort, second_offset;
```

**`sql/analysis/` templates** (these are what the agent executes via MCP; parameterized by `{trailer_id}`):

1. `retention_curve.sql` — `uniqMerge(viewers_state)` per second per cohort, normalized against second 0..2 baseline → retention fraction.
2. `changepoints.sql` — window-function pass over the smoothed retention curve: per-second delta, robust z-score via `quantile(0.5)` (median) and MAD; flag seconds where `abs(z) > 3 AND drop >= 0.03` in a 3s window. Return top-N cliffs with second, drop_pct, affected cohorts.
3. `cohort_divergence.sql` — per-second max spread across cohorts; surfaces cliffs that hit only one demographic.
4. `milestone_funnel.sql` — uses ClickHouse built-ins `retention()` and/or `windowFunnel()` over session milestones (reached 25% / 50% / 75% / complete). This query MUST use these native functions — it is a judged showcase, not decoration.

---

## 6. SYNTHETIC DATA SPEC (ground truth is the test oracle)

`ingest/generate.py` produces realistic data with **known injected cliffs**:

- 3 trailers (`demo_001` 90s, `demo_002` 120s, `demo_003` 75s), ~40–60k sessions each, Poisson arrivals over a 7-day window, cohort/region/device drawn from weighted distributions.
- Baseline retention: smooth exponential-ish decay (e.g., 100% → ~35% at end) + small Gaussian noise per second.
- **Injected cliffs** (the ground truth): per trailer, 2–3 events like `{second: 47, drop_pct: 0.22, cohorts: ['18-24','25-34']}` — implemented as elevated exit probability at that second for those cohorts. Write all injections to `data/ground_truth.json`.
- Each active session emits exactly one `heartbeat` per second watched, one `exit` or `complete` terminal event.
- Deterministic with `--seed`; `--sessions` and `--trailers` flags; streams NDJSON to `data/events/` (never hold full dataset in memory).

`ingest/load.py`: clickhouse-connect batch insert (50k rows/batch), checkpoint file for resume, `--force` to truncate-and-reload, prints row counts per table after load.

---

## 7. AGENT SPEC (Google ADK)

**Reference wiring — validate imports against the installed google-adk version before use:**

```python
# agent/cutpoint_agent/mcp.py
import os
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

def clickhouse_toolset() -> McpToolset:
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "--with", "mcp-clickhouse", "--python", "3.13", "mcp-clickhouse"],
                env={k: os.environ[k] for k in (
                    "CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER",
                    "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE",
                    "CLICKHOUSE_SECURE", "CLICKHOUSE_VERIFY",
                ) if k in os.environ},
            ),
            timeout=120,
        ),
    )
```

**Pipeline: `root_agent = SequentialAgent(sub_agents=[analyst, extractor, diagnostician, reporter])`**, state passed via `output_key` / session state. Each step:

1. **analyst** (`LlmAgent`, model=`GEMINI_MODEL`, tools=[clickhouse_toolset()]): instruction pins it to executing the four `sql/analysis/` templates for the given `trailer_id` via the MCP query tool — it fills parameters and runs them, nothing freeform. Output (pydantic `AnalysisResult`): retention curve summary + ranked cliffs `[{second, drop_pct, cohorts, z}]`.
2. **extractor** (deterministic tool step, no LLM decisions): for each cliff, HTTP POST `SEGMENT_EXTRACTOR_URL/extract` with `{video_path, start_s: max(0, cliff-5), end_s: cliff+5}` → clip paths. Implemented as an ADK FunctionTool / callback so the pipeline order is code-enforced.
3. **diagnostician** (`LlmAgent`, multimodal): for each clip, calls Vertex AI Gemini with the video as a Part (`google-genai` client, `vertexai=True`) + structured prompt: "At second {s}, {drop_pct}% of {cohorts} viewers left. Describe exactly what happens on screen in this ±5s window and give the most plausible causal hypothesis." Output: `Diagnosis{second, on_screen, hypothesis, severity(1-5), confidence}`. Client injected/mocked in tests.
4. **reporter**: merges everything into `DirectorsNotes` (pydantic, see §8), writes `data/reports/{trailer_id}.json`, then renders MD + HTML via `report/render.py`. LLM used only to phrase recut recommendations from the structured diagnoses.

`agent/run_pipeline.py` drives the pipeline end-to-end from CLI (used by `make analyze` and the demo). Structure it so `adk web` / `adk run` also work against `cutpoint_agent` for interactive judging.

`agent/deploy_agent_engine.py`: scripted Agent Engine deployment (agent_engines.create with the ADK app, requirements, env). Must dry-run validate config without deploying when `--dry-run` is passed.

---

## 8. REPORT SCHEMA (frontend API contract)

```python
class RecutRecommendation(BaseModel):
    action: str            # 'trim' | 'reorder' | 'replace_shot' | 'shorten' | 'soften_reveal'
    target_range_s: tuple[int, int]
    rationale: str

class CliffFinding(BaseModel):
    second: int
    drop_pct: float
    affected_cohorts: list[str]
    z_score: float
    clip_path: str
    on_screen: str
    hypothesis: str
    severity: int          # 1..5
    recommendations: list[RecutRecommendation]

class DirectorsNotes(BaseModel):
    trailer_id: str
    title: str
    duration_s: int
    analyzed_at: datetime
    overall_retention_end: float
    milestone_funnel: dict[str, float]      # from the retention()/windowFunnel() query
    cliffs: list[CliffFinding]
    executive_summary: str
```

HTML render: single self-contained file — retention curve (inline SVG from the data, no CDN dependency), cliff markers on a timeline, one card per cliff (screenshot frame optional via ffmpeg `-frames:v 1`).

`api/main.py`: `POST /analyze {trailer_id}` → runs pipeline (sync for prototype) → `{report_id}`; `GET /report/{trailer_id}` → DirectorsNotes JSON; `GET /report/{trailer_id}/html`; `GET /trailers`. CORS open. This is the exact contract `docs/frontend-spec.md` references.

---

## 9. PHASES & ACCEPTANCE GATES

Execute in order. Gate command must exit 0.

**Phase 0 — Scaffold.**
Deliver: full repo layout (§3), LICENSE (MIT), .gitignore (.env, data/, __pycache__, .venv), pyproject via uv, Makefile with all targets stubbed to real commands, .env.example, PROGRESS.md, `scripts/preflight.py` (checks: env vars present, ClickHouse TCP+auth reachable via clickhouse-connect ping, gcloud ADC valid, Vertex model list contains GEMINI_MODEL, ffmpeg/ffprobe on PATH, uv present; prints PASS/FAIL table with fixes).
Gate: `uv sync && make preflight` (preflight may report FAIL items for missing creds but must run cleanly and exit 0 with `--report-only`).

**Phase 1 — Schema + synthetic data + load.**
Deliver: §5 DDL applied by `make schema` (uses clickhouse-connect, ingest path — allowed), §6 generator + loader.
Gate: `make generate-data load && make verify-data` — verify-data asserts row counts > 0 for all tables, and that for each ground-truth cliff a raw SQL check shows viewer count at `second+1` is at least `drop_pct*0.6` below `second-1` (data actually contains the cliffs).

**Phase 2 — Analysis SQL library + detector tests.**
Deliver: the four `sql/analysis/` templates + `tests/test_detector.py` that runs `changepoints.sql` (direct clickhouse-connect in tests is acceptable — tests are not the agent path) and asserts every injected cliff is recovered within ±2 seconds, and false positives ≤ 2 per trailer. `milestone_funnel.sql` must use `retention()` or `windowFunnel()`.
Gate: `make test-analysis`.

**Phase 3 — MCP smoke.**
Deliver: `scripts/mcp_smoke.py` — spawns `mcp-clickhouse` via stdio exactly as the agent will, lists tools, runs `SELECT 1`, runs `retention_curve.sql` for demo_001 through the MCP tool, prints first rows.
Gate: `make mcp-smoke`.

**Phase 4 — Segment extractor service.**
Deliver: FastAPI service (§3 layout): `POST /extract {video_path|gcs_uri, start_s, end_s} → {clip_path, duration_s}` using ffmpeg stream copy where possible; `GET /health`; Dockerfile; `deploy_cloud_run.sh` (gcloud run deploy, `--dry-run` supported); `scripts/fetch_sample_video.sh` downloading a small Creative-Commons sample video (e.g., Blender Foundation short) into `data/videos/` with attribution note, and registering it as demo_001's video.
Gate: `make extractor-test` — starts service, extracts a 10s clip from the sample, asserts ffprobe duration within ±0.5s, then a 3s clip near end-of-file (boundary case).

**Phase 5 — ADK agent core.**
Deliver: §7 in full. Unit tests with mocked Gemini client + mocked extractor verifying: pipeline order is fixed, analyst output schema validates, diagnostician prompt contains the cliff metadata, reporter writes valid `DirectorsNotes`.
Gate: `make test-agent` (mocked) AND `python -m agent.run_pipeline --trailer demo_001 --dry-run` prints the resolved 5-step plan without cloud calls.

**Phase 6 — Report renderer.**
Deliver: `report/render.py` + templates; golden-file test from a fixture `DirectorsNotes` JSON.
Gate: `make test-report` and `data/reports/fixture.html` opens with curve + cliff cards (assert key DOM strings present).

**Phase 7 — End-to-end demo path.**
Deliver: `make demo` = generate (if absent) → load → start extractor → run pipeline live (real ClickHouse via MCP, real Gemini) for demo_001 → write `data/reports/demo_001.{json,md,html}` → print report path + one-line summary per cliff.
Gate: `make demo` completes; report JSON contains every ground-truth cliff for demo_001 (±2s) each with non-empty `hypothesis`.

**Phase 8 — API facade + deploy scripts + Replit handoff.**
Deliver: `api/main.py` per §8; `deploy_agent_engine.py --dry-run` clean; `docs/frontend-spec.md` — a complete, self-contained Replit Agent prompt: product description, exact API contract with sample payloads, screens (trailer picker → analyze progress → report view with timeline + cliff cards), brand direction (dark studio aesthetic, amber accent), and the hard requirement that it deploys on replit.app.
Gate: `make api-test` — httpx tests against the facade with the pipeline mocked: /trailers, /analyze, /report round-trip.

**Phase 9 — Docs + submission pack.**
Deliver: full README (what/why/architecture mermaid/quickstart: five commands from clone to report/env table/screenshots placeholders); `docs/architecture.md`; `docs/demo-video-script.md` (3:00 max, five beats: problem 20s → run `make demo` 40s → report walkthrough with the second-47 story 60s → architecture flash with MCP+ADK+ClickHouse callout 40s → impact close 20s); `docs/submission-checklist.md` mapping every hackathon requirement to its artifact in-repo.
Gate: `make verify-all` = ruff + full pytest + preflight --report-only + a script asserting LICENSE exists, README contains required sections, .env not tracked by git.

---

## 10. DEFINITION OF DONE

- [ ] All phase gates green in `PROGRESS.md` (or explicitly blocked with analysis in `BLOCKERS.md`)
- [ ] `make demo` produces a Director's Notes HTML from live ClickHouse (via MCP) + live Gemini (via Vertex)
- [ ] Zero Anthropic/Claude API references in product code
- [ ] Agent query path touches ClickHouse ONLY through mcp-clickhouse; write path only in `ingest/` and `make schema`
- [ ] `milestone_funnel.sql` uses native `retention()`/`windowFunnel()`
- [ ] `git log` shows one+ commit per phase; `.env` never committed
- [ ] `docs/frontend-spec.md` is pasteable into Replit Agent as-is

Begin with Phase 0. Read `PROGRESS.md` first if it exists.
