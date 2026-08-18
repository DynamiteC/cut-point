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

## Phases

- Phase 0 | in-progress | `uv sync && make preflight-report` | preflight prints PASS/FAIL table and exits 0 | (pending commit)
- Phase 1 | pending | `make generate-data load && make verify-data` | not started | -
- Phase 2 | pending | `make test-analysis` | not started | -
- Phase 3 | pending | `make mcp-smoke` | not started | -
- Phase 4 | pending | `make extractor-test` | not started | -
- Phase 5 | pending | `make test-agent` + dry-run pipeline print | not started | -
- Phase 6 | pending | `make test-report` | not started | -
- Phase 7 | pending | `make demo` | not started | -
- Phase 8 | pending | `make api-test` | not started | -
- Phase 9 | pending | `make verify-all` | not started | -
