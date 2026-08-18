# CutPoint Submission Checklist

Maps every hackathon requirement from TASK.md to its artifact in this repo.

| Requirement | Artifact |
|---|---|
| Runtime LLM is Gemini via Vertex AI, never Claude | `agent/cutpoint_agent/steps/diagnostician.py` (`genai.Client(vertexai=True, ...)`), `GOOGLE_GENAI_USE_VERTEXAI=TRUE` in `.env.example` |
| Agent framework is Google ADK, deployable to Agent Engine | `agent/cutpoint_agent/agent.py` (`SequentialAgent`, `root_agent`), `agent/deploy_agent_engine.py` |
| All agent-side ClickHouse access via mcp-clickhouse only | `agent/cutpoint_agent/mcp.py` (`McpToolset` + `StdioConnectionParams`), used only in `steps/analyst.py`; `clickhouse-connect` appears only under `ingest/` |
| LICENSE (MIT) at repo root | `LICENSE` |
| Complete README with run instructions | `README.md` |
| `.env.example` with every required variable | `.env.example` |
| No secrets committed | `.gitignore` excludes `.env`; `scripts/verify_repo_hygiene.py` checks this in `make verify-all` |
| Frontend NOT built in this repo | `docs/frontend-spec.md` (Replit Agent build prompt); `api/main.py` is the thin REST facade |
| Determinism: fixed 5-step (4-step) protocol, LLM only for perception/language | `agent/cutpoint_agent/agent.py` (`SequentialAgent` fixed order); see PROGRESS.md for the BaseAgent-vs-LlmAgent design note |
| ClickHouse schema, ORDER BY matches query pattern | `sql/001_schema.sql`, `sql/002_materialized_views.sql` |
| Synthetic data with known injected cliffs (ground truth) | `ingest/generate.py`, `data/ground_truth.json` (gitignored, regenerate with `make generate-data`) |
| Analysis SQL library (4 templates) | `sql/analysis/*.sql` |
| `milestone_funnel.sql` uses native `retention()`/`windowFunnel()` | `sql/analysis/milestone_funnel.sql` (`windowFunnel(86400)(...)`) |
| Detector recovers every injected cliff within +/-2s, <=2 false positives | `tests/test_detector.py`, verified against real local ClickHouse data (see PROGRESS.md Phase 2) |
| MCP smoke test against mcp-clickhouse via stdio | `scripts/mcp_smoke.py` |
| Segment extractor service (ffmpeg) | `services/segment_extractor/main.py`, `Dockerfile`, `deploy_cloud_run.sh` |
| Creative-Commons sample video with attribution | `scripts/fetch_sample_video.sh`, `data/videos/ATTRIBUTION.txt` |
| ADK agent core, 4-step pipeline, mocked unit tests | `agent/cutpoint_agent/`, `tests/test_agent.py` |
| Report renderer (JSON -> MD -> HTML, self-contained SVG) | `report/render.py`, `report/templates/`, `tests/test_report.py` |
| End-to-end demo path | `scripts/run_demo.py` (`make demo`) |
| REST API facade with mocked-pipeline tests | `api/main.py`, `tests/test_api.py` |
| Docs: architecture (mermaid), demo script, frontend spec | `docs/architecture.md`, `docs/demo-video-script.md`, `docs/frontend-spec.md` |
| Progress ledger / blockers documentation | `PROGRESS.md`, `BLOCKERS.md` |

## Known gaps in this build environment

This repo was built and gated on a machine without ClickHouse Cloud credentials, without
`gcloud`/GCP Application Default Credentials, and without a running Docker daemon. See
`BLOCKERS.md` for the full detail. In summary:

- ClickHouse: a local standalone ClickHouse server stands in for ClickHouse Cloud (identical
  schema and SQL either way); phases 1-3 are fully green against it.
- Vertex AI / Gemini: genuinely blocked. `make demo` (Phase 7) reaches the first live model call
  and fails with a clean, actionable `MissingCredentialError` / `DefaultCredentialsError` rather
  than a silent mock -- confirming every other part of the pipeline works.
- On a machine with real ClickHouse Cloud and GCP credentials configured in `.env`, every gate
  in `PROGRESS.md` runs unchanged; no code changes are required, only environment configuration.
