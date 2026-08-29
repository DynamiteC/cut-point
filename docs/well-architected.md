# Well-Architected Assessment

Assessed against the **Google Cloud Well-Architected Framework**: its five pillars plus the
AI and ML perspective, which is why this is often counted as six.

Every row names a file or a command. Where something is weak it says so; a self-assessment that
scores itself full marks is not worth reading.

---

## 1. Operational excellence

| | |
|---|---|
| Deployment | `deploy/deploy_all.sh` creates every resource, is idempotent, and has a `--dry-run` that prints each command without executing. `deploy/teardown.sh` removes it all. |
| Reproducibility | `README.md` Quickstart: five commands from clone to a rendered report. `make preflight` checks every prerequisite and prints a fix line per failure. |
| Observability | `agent/cutpoint_agent/obs.py` emits Cloud Logging JSON with `severity`, `message` and a `run_id` that correlates every line of one pipeline run. Operational paths (scan complete, scan failed, analyst degraded, pipeline start/complete/failed, ffmpeg and ffprobe failures) go through it. |
| Config validation | `api/main.py::_validate_cloud_config` refuses to start a cloud deployment missing `GOOGLE_CLOUD_PROJECT`, `GEMINI_MODEL` or `CLICKHOUSE_HOST`, and warns loudly if auth has been disabled on a deployed revision. |
| Health | `GET /health` on the API and watcher. Not `/healthz`: Google's frontend intercepts that path on Cloud Run and answers 404 without reaching the container. |

**Gaps, stated plainly.** There is no CI. Nothing runs the 75 tests automatically on push. There
are no metrics and no dashboards, only logs, so there is no request-duration histogram or error
rate per endpoint. ADK 2.x emits OpenTelemetry spans natively and they are not exported to Cloud
Trace; that is the single highest-value remaining operational improvement.

---

## 2. Security, privacy and compliance

| | |
|---|---|
| Authentication | `api/auth.py`. Google-signed OIDC, issuer checked, audience pinned to the service's own URL, caller pinned to an allowlist of service accounts. Fails closed: auth is on unless explicitly disabled. |
| Authorization boundary | Read endpoints public so the UI needs no credential; every endpoint that spends money is authenticated. The watcher and extractor are private at the platform level. |
| Least privilege | The extractor is `--no-allow-unauthenticated` because it shells out to ffmpeg. GCS reads are confined to the configured bucket, since the service account holds `storage.objectAdmin` project-wide. |
| Injection | `trailer_id` and `job_id` constrained to `^[a-zA-Z0-9_-]{1,64}$` at the API boundary, and re-validated in `store.py` and the watcher for callers that do not come through FastAPI. `changepoints.sql` interpolates the id into query text, so ids read out of the database are validated too. |
| Database access | Agent queries go through `mcp-clickhouse`, read-only by construction. The validator and watcher additionally pin `readonly=1` at the session level, so the server refuses a write (verified: ClickHouse error 164). Read-write `clickhouse-connect` is confined to `ingest/`. |
| Output encoding | The HTML report autoescapes. It did not: `select_autoescape(["html"])` matches the final extension and the template is `report.html.jinja`, so free-form model output was written verbatim into a page served as `text/html`. |
| Data exposure | `GET /jobs/{id}` returns an allowlist of progress fields. Raw exception text and caller identity are never served anonymously. ffmpeg and ffprobe stderr, which echoes the input URI, is logged rather than returned. |
| Secrets | No credentials in code. `.env` is gitignored, never committed, and excluded from both the Docker build context and the gcloud source upload. |

**Gaps.** `CLICKHOUSE_PASSWORD` is passed as a Cloud Run environment variable rather than through
Secret Manager, which is acceptable for a short-lived hackathon deployment and would not be in
production. There is no rate limiting per caller, only a global concurrency cap. TLS to
ClickHouse Cloud has never been exercised.

---

## 3. Reliability

| | |
|---|---|
| Failure isolation | One clip failing to diagnose is recorded and skipped; it does not discard the other findings or the extraction spend behind them. All clips failing still raises, because that is an outage and not a report. |
| Model failure | The analyst is wrapped so that its failure writes an empty result and continues. The validator then supplies every number from the database. A hallucinating, timing-out or truncating model cannot end a run or corrupt a report. |
| Idempotency | The watcher fingerprints the cliff set in Firestore and publishes only on a real change. A redelivered Pub/Sub message for a completed job is a no-op rather than a re-spend. |
| Durable state | Reports, jobs and fingerprints live in Firestore, not on a Cloud Run instance's ephemeral disk. |
| Timeouts | 120s on the Vertex call, 30s on the extractor HTTP call, 10s on ClickHouse connect, 600s Pub/Sub ack deadline covering a full pipeline run. |
| Bounded retries | A failed scan returns 200 with `status: degraded` rather than a 5xx, because Pub/Sub redelivers on 5xx and an unreachable database is not something a redelivery fixes. Retention is capped at one hour with 60-600s backoff. |
| Data integrity | Ingest checkpoints carry a fingerprint of the file they belong to, so a regenerated events file cannot cause the loader to skip the head of the new one. |
| Tested | 75 tests, including four chaos scenarios (ClickHouse unreachable, extractor down, corrupt video, Gemini timeout). |

**Gaps.** No multi-region anything. `--max-instances=1` is a cost choice that is also a
single point of failure. No dead-letter topic; a permanently poisonous message is dropped when
retention expires rather than quarantined for inspection.

---

## 4. Cost optimization

| | |
|---|---|
| Scale to zero | All three services `--min-instances=0 --max-instances=1`. |
| Work avoidance | The watcher's fingerprint is the main cost control: an unchanged trailer triggers nothing, and a trailer with zero cliffs never starts a pipeline at all. |
| Hard ceiling | `CUTPOINT_MAX_ANALYSES_PER_DAY` (default 25), counted with an atomic Firestore increment, reserved before a run and refunded when the run does not happen. |
| Concurrency | `CUTPOINT_MAX_CONCURRENT_PIPELINES` (default 2), shedding with 429. Without it FastAPI's 40-slot threadpool allowed 40 simultaneous pipelines per instance. |
| Scheduled spend | The Cloud Scheduler job is created **paused**. An enabled `*/15` tick wakes the watcher 96 times a day indefinitely. |
| Teardown | `deploy/teardown.sh`, with `--purge-data` for Firestore and the bucket. |

**Measured.** A log audit found 714 watcher invocations in two hours, of which the scheduler
explained 8. The rest was Pub/Sub redelivering 5xx responses, retained for the default seven
days. That is what drove the retention cap, the backoff, and the degraded-path 200.

---

## 5. Performance optimization

| | |
|---|---|
| Analytics | Per-second unique viewers come from an `AggregatingMergeTree` materialized view using `uniqState`/`uniqMerge` rather than counting raw events per query. Milestone funnel uses native `windowFunnel()`. Change-point detection is a MAD-based robust z-score, resistant to noisy counts. |
| Determinism | `milestone_funnel.sql` uses `argMax(duration_s, created_at)` rather than `LIMIT 1` without `FINAL`, which on a `ReplacingMergeTree` returned whichever part was read first and silently changed results between runs. |
| Memory | In the cloud the clip is passed to Gemini by `gs://` URI rather than inline bytes, keeping whole videos out of the service's memory. |
| Connections | The extractor's HTTP client is owned by a context manager. It previously leaked a client and pool per pipeline run on a long-lived instance. |
| Model budget | The analyst is capped at 32768 output tokens with a 1024-token thinking budget, after it spent an 8192 cap on reasoning and truncated its answer at column 50. |
| Measured | `docs/perf/`: p99 API latency 102ms at 50 concurrent, 268k rows/sec ingest. |

**Gaps.** Diagnoses run serially, one Gemini call per cliff. Parallelising them would cut
wall-clock roughly linearly and is the obvious next win. There is no caching: re-analysing a
trailer whose report already exists re-runs the whole pipeline.

---

## 6. AI and ML perspective

| | |
|---|---|
| The model is not trusted with facts | The analyst transcribes tool output into a schema, and `output_schema` validates shape, not numbers. The validator re-derives cliffs, funnel and retention from ClickHouse and overrules it. On a real run the analyst reported a cliff at second 2 that does not exist and missed all three that do. |
| The model is not trusted with control flow | Step order is fixed in code by `SequentialAgent`. No model chooses what runs next. |
| The model is not a dependency | A failing analyst is contained, not fatal. |
| Grounded perception | Gemini is asked only about frames that a statistically detected cliff points at, so it never speculates about a moment the data did not flag. |
| Prompt correctness | The diagnostician prompt is clip-relative. Addressing the model with an absolute trailer timestamp for a clip whose timeline starts at 00:00 made it refuse, and that refusal was written into a report as a diagnosis. |
| Output handling | Model output is treated as untrusted text: schema-validated, severity and confidence clamped to range, and HTML-escaped at render. |
| Model selection | `gemini-3.5-flash`, a Flash-class model for a perception task, verified servable by a real call rather than a catalog listing. |

**Gaps.** There is no evaluation harness for diagnosis quality. Detection accuracy is measured
against injected ground truth with `demo_control` as a non-circular false-positive control, but
whether Gemini's causal hypothesis is *correct* is unmeasured and currently unmeasurable without
human judgement. No guardrail product (Model Armor) is in use; the containment here is
architectural rather than an inline filter.
