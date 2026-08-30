# Submission Checklist

Requirement to artifact, for both entries. Every row names something a judge can open.

## All Things Agentic: mandatory technology

| Requirement | Artifact | Status |
|---|---|---|
| Gemini 3.5 or newer via Gemini API or Vertex AI | `agent/cutpoint_agent/config.py` (`gemini-3.5-flash`), `steps/diagnostician.py` (`genai.Client(vertexai=True)`) | `make preflight` verifies this. It makes a real call. It does not trust a catalog listing |
| At least one Google agent framework | Google ADK is the agent framework: `agent/cutpoint_agent/agent.py` (`SequentialAgent`, `LlmAgent`, `BaseAgent`, `McpToolset`). The GenAI SDK (`google-genai`) makes the direct Vertex AI calls. | Google ADK meets the requirement. The GenAI SDK is the model client. It does not count as a second framework |
| At least one Google Cloud infrastructure service | **Cloud Run**, **Pub/Sub**, **Firestore**, created by `deploy/deploy_all.sh` | Three of the five services that the rules name. CutPoint uses Vertex AI and Cloud Storage too, but does not count them |

## All Things Agentic: what to submit

| Requirement | Artifact |
|---|---|
| Category | The Taskmaster |
| Hosted project URL | https://dynamitec.github.io/cut-point/app.html (the UI reads the API). API direct: `https://cutpoint-api-nlfe4x5pnq-uc.a.run.app`. The service scales to zero, so the first request can be slow to start |
| Text description | `docs/DEVPOST_SUBMISSION.md` |
| Public code repository | https://github.com/DynamiteC/cut-point |
| Spin-up instructions in README | `README.md`, Quickstart. It runs locally in five commands. It also has a scripted cloud deploy and teardown |
| Architecture diagram | `docs/architecture.md` (Mermaid), `submission/gallery/01_system_architecture.jpg` |
| Roughly 4-minute demo video | `docs/demo-video-script.md` |
| Proof the backend runs on Google Cloud | The video shows the Cloud Run dashboard, the Pub/Sub subscriptions, the Firestore documents, the Vertex AI logs, and a live `.run.app` URL |

## Judging criteria to evidence

| Criterion | Weight | Where it is demonstrated |
|---|---|---|
| Innovation and operational utility | 40% | The watcher does a multi-step workflow with no human step: Cloud Scheduler, then Pub/Sub, then the fingerprint difference, then the full diagnosis. The fingerprint makes it an agent, not a cron job |
| Architectural discipline and tech stack | 30% | The code sets the step order. A model never sets it. No model touches the numbers: step 1 reads ClickHouse over a `readonly=1` connection. A grounding check tests the model's language output and rejects it if it names a cliff that was not detected. Auth uses OIDC, audience pinning and an invoker allowlist. The system has a concurrency cap and a daily budget limit. It keeps state in Firestore, not on a temporary disk |
| Demo and production readiness | 30% | 89 tests, including four chaos tests. See `docs/perf/`. Honest limitations are in `docs/BLOCKERS.md`. The setup is reproducible. The deploy and the teardown are scripted |

## Agentic Cinema

| Requirement | Artifact |
|---|---|
| Gemini and Google Cloud | As above |
| ClickHouse partner track | `sql/analysis/`: MAD z-score change-point detection, `AggregatingMergeTree` with `uniqState`/`uniqMerge`, and native `windowFunnel()`. The analyst and the watcher read over `readonly=1`. The narrator reads through `mcp-clickhouse`, which is read-only by design |
| Film and media workflow | The system diagnoses trailer retention. It produces Director's Notes with a recut action for each cliff |

## Honesty

We disclose this in both submissions: the same codebase is entered in the other. We wrote all
code between 19 and 31 August 2026, inside both submission periods. The data is synthetic.
`demo_control` is a false-positive control that the detector was not tuned on. We list the known
limitations in `docs/BLOCKERS.md`. We do not hide them.
