# CutPoint

A deterministic multi-step AI agent that fuses per-second trailer audience-retention analytics
(ClickHouse) with Gemini's frame-level video understanding to tell a studio marketing team
exactly WHERE a trailer loses viewers and WHY, then emits timestamped "Director's Notes" with
recut recommendations.

The loop: ClickHouse answers WHERE (second 47, cohort 18-24, 22% retention cliff) -> Gemini
multimodal answers WHY (tone shift / spoiler / pacing collapse in those exact frames) -> the
agent renders Director's Notes + a recut plan.

Built for the Agentic Cinema hackathon (Google Cloud, ClickHouse track).

## Quickstart

Five commands from clone to a rendered report (assumes ClickHouse and GCP credentials are set
in `.env` -- see [Environment variables](#environment-variables)):

```bash
uv sync
make schema
make generate-data load
make extractor-test              # sanity check the segment extractor works locally
make demo                        # generates the report at data/reports/demo_001.html
```

Run `make preflight` at any time to see exactly which prerequisites are missing and how to fix
each one.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full write-up and a mermaid diagram.
In short:

```
ClickHouse Cloud --(mcp-clickhouse, read-only)--> analyst
                                                      |
                                                      v
                                                  extractor --(ffmpeg)--> clip
                                                      |
                                                      v
                                            diagnostician --(Gemini via Vertex AI)--> diagnosis
                                                      |
                                                      v
                                                  reporter --> Director's Notes (JSON/MD/HTML)
```

The pipeline is a Google ADK `SequentialAgent`: step order is fixed by code, never chosen by an
LLM. The LLM is used only for perception (what's happening in a video clip) and language
(phrasing recommendations).

## Repository layout

| Path | Purpose |
|---|---|
| `sql/` | ClickHouse schema, materialized views, and the four analysis query templates |
| `ingest/` | Synthetic data generator + loader (the only place `clickhouse-connect` is used directly) |
| `agent/` | The Google ADK agent: `cutpoint_agent/agent.py` is the `root_agent` entrypoint |
| `services/segment_extractor/` | FastAPI + ffmpeg service that clips video around a retention cliff |
| `api/` | Thin REST facade for the frontend |
| `report/` | Renders Director's Notes JSON into Markdown and self-contained HTML |
| `scripts/` | Preflight checks, MCP smoke test, sample video fetcher, demo runner |
| `tests/` | Unit and integration tests (mocked Gemini/extractor where noted) |
| `docs/` | Architecture doc, Replit frontend build prompt, demo script, submission checklist |

## Environment variables

Copy `.env.example` to `.env` and fill in every value:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `CLICKHOUSE_HOST` / `CLICKHOUSE_PORT` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` / `CLICKHOUSE_DATABASE` | ClickHouse Cloud (or any ClickHouse server) connection |
| `CLICKHOUSE_SECURE` / `CLICKHOUSE_VERIFY` | TLS settings for the ClickHouse connection |
| `GOOGLE_GENAI_USE_VERTEXAI` | Must be `TRUE` -- the product calls Gemini through Vertex AI, never the direct Gemini API or Anthropic |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | GCP project and region for Vertex AI |
| `GEMINI_MODEL` | Gemini model id, validated against your project by `make preflight` |
| `SEGMENT_EXTRACTOR_URL` | Where the segment extractor service is reachable (local or Cloud Run) |
| `GCS_BUCKET` | Optional; blank means clips are stored locally under `data/clips/` |
| `API_PORT` | Port for the REST facade (`api/main.py`) |

## Running the full phase gates

Each phase in `PROGRESS.md` has a gate command:

```bash
make preflight-report   # phase 0
make generate-data load && make verify-data   # phase 1
make test-analysis      # phase 2
make mcp-smoke          # phase 3
make extractor-test     # phase 4
make test-agent         # phase 5
make test-report        # phase 6
make demo               # phase 7 (needs live ClickHouse + Vertex AI)
make api-test           # phase 8
make verify-all         # phase 9
```

See `PROGRESS.md` for current status per phase and `BLOCKERS.md` for anything that requires
credentials not available in a given environment.

## Development notes

- This repository does not build a frontend. `docs/frontend-spec.md` is a complete, pasteable
  build prompt for Replit Agent.
- All agent-side ClickHouse access goes through the official `mcp-clickhouse` MCP server (see
  `agent/cutpoint_agent/mcp.py`), which is read-only by design. Direct `clickhouse-connect` use
  is confined to `ingest/` (the write/load path) and `make schema`.
- Mocks live only in `tests/`. Where cloud credentials are absent, the code degrades with an
  explicit, actionable error (e.g. `MissingCredentialError: set CLICKHOUSE_HOST in .env`), never
  a silent stub.

## License

MIT -- see [LICENSE](LICENSE).
