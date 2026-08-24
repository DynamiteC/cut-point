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

## Post-phase-9 hardening (ship-ready-review)

A ship-ready-review pass (security/error-handling/production-readiness focus) found and fixed
three issues before this was considered ready for /ship-ready-review + /devils-advocate
sign-off:

1. **Path traversal in the segment extractor** (`services/segment_extractor/main.py`):
   `video_path` was accepted with no root confinement -- an absolute path anywhere on the
   filesystem, or a `../`-escaping relative path, would be processed by ffmpeg/ffprobe. Fixed
   by resolving the path and requiring it be inside `data/videos/` (`Path.is_relative_to`);
   returns 400 otherwise. Covered by
   `test_extract_path_traversal_outside_videos_dir_rejected`.
2. **Unvalidated `trailer_id` on the API facade** (`api/main.py`): `trailer_id` flows into
   filesystem paths (`data/reports/{trailer_id}.json`) and, in the live pipeline, into SQL query
   text the analyst LLM constructs from `sql/analysis/*.sql` templates -- an unvalidated value
   is both a path-traversal and a prompt/SQL-injection-shaped risk at the API boundary. Fixed by
   constraining `trailer_id` to `^[a-zA-Z0-9_-]{1,64}$` on `POST /analyze` and both
   `GET /report/{trailer_id}` routes. Covered by `test_analyze_rejects_path_traversal_trailer_id`
   and `test_report_rejects_path_traversal_trailer_id`.
3. **String-interpolated cohort list in `ingest/verify_data.py`**: cohorts came from
   developer-controlled `ground_truth.json` (low risk) but were spliced into SQL text via
   f-string rather than the parameterized-query pattern used everywhere else in the file;
   switched to a `%(cohorts)s` tuple parameter for consistency and defense in depth.

Accepted, not fixed (explicitly required by TASK.md / inherent to the hackathon prototype
scope, tracked for a real deployment): CORS is open and no auth exists on any API endpoint
(TASK.md section 8 requires "CORS open"), so a production deployment would need auth + rate
limiting in front of `/analyze` before it could be exposed publicly, since each call triggers
real Vertex AI spend.

## Post-hardening devils-advocate pass

A devils-advocate pass raised five concerns. Two were fixed; three are documented limitations
inherent to the TASK.md-mandated design or the credential-free build environment (see
BLOCKERS.md for the latter two):

1. **[Fixed] Circular validation of detector thresholds.** `changepoints.sql`'s thresholds
   (z-score > 3, drop_pct >= 0.03, cohort-attribution factor 0.5) were tuned against the exact
   3 synthetic trailers the detector is graded on, and `MAX_FALSE_POSITIVES = 2` in
   `tests/test_detector.py` was never actually exercised because the generator never produced an
   organic (non-injected) false cliff -- the test was structurally incapable of catching an
   over-sensitive detector. Fixed by adding a fourth trailer, `demo_control` (see
   `ingest/generate.py`), with ZERO injected cliffs -- pure baseline decay + per-second noise --
   and a new test, `test_changepoints_false_positive_rate_on_control_trailer`, that asserts the
   detector flags at most `MAX_FALSE_POSITIVES` cliffs on data its thresholds were never fitted
   to. Result: 0 false positives on the control trailer, a genuine (non-circular) signal that
   the detector is not simply overfit to the three graded fixtures.
2. **[Documented, see BLOCKERS.md] Stand-in demo video has no narrative connection to the
   injected cliff seconds.** Even with live Vertex AI credentials, `data/videos/demo_001.mp4`
   is a looped 10s Creative-Commons animation clip with no scene changes correlated to seconds
   22/47/68 -- so the diagnostician's "on_screen"/"hypothesis" output for this specific demo
   video would describe arbitrary frames, not a coherent causal story. The pipeline mechanics
   (SQL -> extraction -> Gemini call -> report) are real and would produce a genuinely useful
   WHY narrative against an actual trailer where cuts and cliffs correlate; only the *content*
   of this specific stand-in video is disconnected from the synthetic retention data.
3. **[Documented, see BLOCKERS.md] The TLS/secure ClickHouse connection path is never
   exercised.** All local testing uses `CLICKHOUSE_SECURE=false` against the local ClickHouse
   binary; the `secure=True, verify=True` code path used for real ClickHouse Cloud has never
   actually run in this environment.
4. **[Accepted tradeoff, not a defect] The analyst step reports quantitative findings (which
   seconds are cliffs, exact drop_pct/z_score) via LLM transcription of MCP tool output into a
   pydantic schema** (`agent/cutpoint_agent/steps/analyst.py`), not code-level parsing of the
   `run_query` tool's JSON response. `output_schema` validates shape/type, not that the reported
   numbers exactly match what ClickHouse returned -- an LLM transcription error (misreading a
   row, transposing a value) would not be caught by schema validation alone. This is inherent to
   TASK.md section 7's own mandated design (`analyst` MUST be an `LlmAgent` with the
   mcp-clickhouse toolset and `output_schema=AnalysisResult`), not a deviation introduced during
   this build, so it was not changed. A production hardening path worth calling out for future
   work: parse `run_query`'s JSON response deterministically in code (as `extractor` already
   does for its HTTP calls) and reserve the LLM turn for cases that genuinely need judgment.
5. **[Accepted tradeoff, already documented above] BaseAgent instead of LlmAgent for
   extractor/diagnostician/reporter reduces the number of LLM-visible reasoning turns an
   `adk web`/`adk run` trace shows** (only `analyst` is a full LLM turn). This was a deliberate
   determinism/testability tradeoff explained in the "Agent design deviation" note above; noted
   here only because a hackathon demo optimizing for "looks agentic" might prefer more visible
   LLM turns even at the cost of the determinism guarantee TASK.md rule 6 asks for.

## Phases

- Phase 0 | done | `uv sync && make preflight-report` | exits 0, PASS/FAIL table with fixes shown | 372285e
- Phase 1 | done | `make generate-data load && make verify-data` | 8.1M rows loaded, all 7 injected cliffs verified | 899de35
- Phase 2 | done | `make test-analysis` | 6/6 tests pass, all cliffs recovered within +/-2s, 0 false positives | e9dff4e
- Phase 3 | done | `make mcp-smoke` | stdio session lists tools, SELECT 1 and retention_curve.sql both succeed via run_query | d8be05b
- Phase 4 | done | `make extractor-test` | 5/5 tests pass (10s clip, boundary 3s clip, 404, gs:// 501) | 41d6a1f
- Phase 5 | done | `make test-agent` + dry-run pipeline print | 6/6 tests pass (mocked Gemini + extractor), dry-run prints 4-step plan | 6e8e813
- Phase 6 | done | `make test-report` | golden-file test passes, fixture html contains curve + cliff cards | abbdc04
- Phase 7 | blocked | `make demo` | reaches live analyst model call, fails with clean MissingCredentialError (no GCP ADC) -- see BLOCKERS.md | 100faf7
- Phase 8 | done | `make api-test` | 7/7 tests pass (trailers, analyze+report round-trip, html, path-traversal x2, 404, cors) | 352a78b
- Phase 9 | done | `make verify-all` | ruff clean, 30/30 tests pass, preflight-report exits 0, repo hygiene passed | 8fc5707
- Phase 10.1 | done | `make smoke` | all 6 checks pass (ClickHouse, MCP tools, extractor health, API /trailers, pipeline dry-run, cleanup) | pending
- Phase 10.2 | done | `make load-test` | p99 at 50 concurrent /trailers: 101.69ms (threshold 2000ms), 100k batch: 267,891 rows/sec, load-chart.png generated | pending
- Phase 10.3 | done | `make stress-test` | API and extractor concurrency ceilings measured, stress-report.md generated | pending
- Phase 10.4 | done | `make chaos-test` | 4/4 chaos scenarios pass (extractor kill, wrong port, corrupt video, Gemini timeout) | pending
- Phase 10.5 | done | `make soak-test-short` | 30-min soak, memory growth 0.9% (threshold 20%), no leaked handles | pending

## Post-phase-9 hardening

- ship-ready-review fixes | done | `make test` (targeted) | path traversal, unvalidated trailer_id, SQL string interpolation all fixed | 352a78b
- devils-advocate fixes | done | `uv run pytest -q` | added demo_control false-positive trailer, documented TLS/demo-video/analyst-transcription tradeoffs | fd36342
- final merge to main | done | `git log origin/main` | feature/cutpoint-prototype merged (no-ff) and pushed to origin/main | ab7bbb9
