# Submission Checklist

Requirement to artifact, for both entries. Every row names something a judge can open.

## All Things Agentic: mandatory technology

| Requirement | Artifact | Status |
|---|---|---|
| Gemini 3.5 or newer via Gemini API or Vertex AI | `agent/cutpoint_agent/config.py` (`gemini-3.5-flash`), `steps/diagnostician.py` (`genai.Client(vertexai=True)`) | Verified by `make preflight`, which makes a real call rather than trusting a catalog listing |
| At least one Google agent framework | Google ADK: `agent/cutpoint_agent/agent.py` (`SequentialAgent`, `LlmAgent`, `BaseAgent`, `McpToolset`). GenAI SDK: `google-genai` | Two of the four accepted frameworks |
| At least one Google Cloud infrastructure service | **Cloud Run**, **Pub/Sub**, **Firestore**, created by `deploy/deploy_all.sh` | Three of the five the rules name. Vertex AI and Cloud Storage are used but not counted |

## All Things Agentic: what to submit

| Requirement | Artifact |
|---|---|
| Category | The Taskmaster |
| Hosted project URL | https://dynamitec.github.io/cut-point/app.html (UI, reads the live API). API direct: `https://cutpoint-api-nlfe4x5pnq-uc.a.run.app` |
| Text description | `docs/DEVPOST_SUBMISSION.md` |
| Public code repository | https://github.com/DynamiteC/cut-point |
| Spin-up instructions in README | `README.md`, Quickstart: local in five commands, plus a scripted cloud deploy and teardown |
| Architecture diagram | `docs/architecture.md` (Mermaid), `submission/gallery/01_system_architecture.jpg` |
| Roughly 4-minute demo video | `docs/demo-video-script.md` |
| Proof the backend runs on Google Cloud | Shown in the video: Cloud Run dashboard, Pub/Sub subscriptions, Firestore documents, Vertex AI logs, a live `.run.app` URL |

## Judging criteria to evidence

| Criterion | Weight | Where it is demonstrated |
|---|---|---|
| Innovation and operational utility | 40% | The watcher completes a multi-step workflow with no human step: Cloud Scheduler to Pub/Sub to fingerprint diff to full diagnosis. The fingerprint is what makes it an agent rather than a cron job |
| Architectural discipline and tech stack | 30% | Step order fixed in code, never chosen by a model, and no model in the numeric path at all: step 1 reads ClickHouse over a `readonly=1` connection. The model's language output is grounding-checked and rejected if it cites an undetected cliff. Auth is OIDC with audience pinning and an invoker allowlist. Concurrency cap and daily budget ceiling. State in Firestore, not on an ephemeral disk |
| Demo and production readiness | 30% | 82 tests including four chaos scenarios, `docs/perf/`, honest limitations in `docs/BLOCKERS.md`, reproducible setup, scripted deploy and teardown |

## Agentic Cinema

| Requirement | Artifact |
|---|---|
| Gemini and Google Cloud | As above |
| ClickHouse partner track | `sql/analysis/`: MAD z-score change-point detection, `AggregatingMergeTree` with `uniqState`/`uniqMerge`, native `windowFunnel()`. Agent access via `mcp-clickhouse`, read-only |
| Film and media workflow | Trailer retention diagnosis producing Director's Notes with per-cliff recut actions |

## Honesty

Disclosed in both submissions: the same codebase is entered in the other. All code was written
19-31 August 2026, inside both submission periods. Data is synthetic, with `demo_control` as a
non-circular false-positive control. Known limitations are listed in `docs/BLOCKERS.md` rather
than omitted.
