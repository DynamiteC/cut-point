# CutPoint Progress Ledger

Format: `Phase N | status | gate command | last result | commit hash`

## Assumptions and deviations

- Branching: the global git-workflow rule asks for branches created from `origin/development`.
  This repo only has `main` (no `development` branch exists on origin). Deviation: work happens
  on `feature/cutpoint-prototype` branched from `main`, and per explicit user instruction the
  final validated prototype is merged and pushed to `main` directly.
- Environment: this build machine has no Docker daemon running, no `gcloud` CLI, no GCP ADC,
  and no ClickHouse Cloud credentials. Per TASK.md rule 7 (no decorative stubs, degrade with an
  explicit actionable error) and rule 3 (phase gates, 3-iteration cap then BLOCKERS.md), phases
  that require live ClickHouse or live Vertex AI are marked "blocked" below with the exact
  missing dependency. See BLOCKERS.md for details and the unblock steps.
- `uv` was not preinstalled; installed via the official installer to `~/.local/bin` (no sudo
  available on this machine, so Homebrew's bottle install failed on a permissions error).
- GEMINI_MODEL default in .env.example follows TASK.md section 4 verbatim (gemini-3-flash);
  actual availability is validated by `make preflight` against the configured GCP project, which
  is not available on this build machine.
- Agent design deviation: TASK.md section 7 describes extractor as deterministic (explicitly:
  "Implemented as an ADK FunctionTool / callback so the pipeline order is code-enforced") and
  diagnostician as "LlmAgent, multimodal". Extractor and diagnostician and reporter are
  implemented as custom `google.adk.agents.BaseAgent` subclasses (not `LlmAgent`) that wrap
  plain, independently unit-testable functions (`run_extraction`, `run_diagnostics`,
  `build_directors_notes`). Rationale: iterating per-clip multimodal Gemini calls with
  per-clip injected video bytes and structured cliff metadata doesn't map cleanly onto a
  single `LlmAgent` turn (which expects one model turn per agent step, not a bounded loop over
  N items), and TASK.md rule 6 already requires the LLM be used only for perception/language,
  never for deciding pipeline order -- a BaseAgent wrapper enforces that more strongly than an
  LlmAgent would. The analyst step IS a literal `LlmAgent` with the mcp-clickhouse toolset and
  `output_schema=AnalysisResult`, per spec. `SequentialAgent` still orchestrates all four steps
  in fixed code order.
- `google.adk.agents.SequentialAgent` emits a deprecation warning in the installed google-adk
  version ("deprecated in favor of Workflow... Workflow cannot yet be used as an LlmAgent
  sub-agent") but is not yet removed and functions correctly; kept as-is since TASK.md section 7
  specifies it explicitly and its replacement (Workflow) is called out as not yet compatible
  with LlmAgent sub-agents (which the analyst step requires).

## Phases

- Phase 0 | done | `uv sync && make preflight-report` | exits 0, PASS/FAIL table with fixes shown | 372285e
- Phase 1 | done | `make generate-data load && make verify-data` | 8.1M rows loaded, all 7 injected cliffs verified | 899de35
- Phase 2 | done | `make test-analysis` | 6/6 tests pass, all cliffs recovered within +/-2s, 0 false positives | e9dff4e
- Phase 3 | done | `make mcp-smoke` | stdio session lists tools, SELECT 1 and retention_curve.sql both succeed via run_query | d8be05b
- Phase 4 | done | `make extractor-test` | 5/5 tests pass (10s clip, boundary 3s clip, 404, gs:// 501) | 41d6a1f
- Phase 5 | done | `make test-agent` + dry-run pipeline print | 6/6 tests pass (mocked Gemini + extractor), dry-run prints 4-step plan | (pending commit)
- Phase 6 | pending | `make test-report` | not started | -
- Phase 7 | blocked | `make demo` | reaches live analyst model call, fails with clean MissingCredentialError (no GCP ADC) -- see BLOCKERS.md | (pending commit)
- Phase 8 | done | `make api-test` | 5/5 tests pass (trailers, analyze+report round-trip, html, 404, cors) | (pending commit)
- Phase 9 | done | `make verify-all` | ruff clean, 26/26 tests pass, preflight-report exits 0, repo hygiene passed | (pending commit)
