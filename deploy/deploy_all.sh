#!/usr/bin/env bash
#
# Deploy the CutPoint async control plane to Google Cloud.
#
# Target architecture:
#   Cloud Scheduler (*/15m) -> Pub/Sub cutpoint-retention-scan -> Cloud Run cutpoint-watcher
#     -> (new cliff found) -> Pub/Sub cutpoint-analyze -> Cloud Run cutpoint-api
#        -> ADK pipeline (analyst / extractor / diagnostician / reporter)
#        -> Director's Notes to Firestore, media to GCS
#
# Qualifying Google Cloud infrastructure services: Cloud Run, Pub/Sub, Firestore.
# Cloud Scheduler and GCS are supporting.
#
# Cost posture (the $150 credit form closed; this is real billing):
#   - every service is --min-instances=0 --max-instances=1
#   - run deploy/teardown.sh after judging to stop all charges
#
# Idempotent: safe to re-run. Each resource is created only if absent.
# Nothing here is destructive; teardown.sh is the only script that deletes.
#
# Usage:
#   ./deploy/deploy_all.sh            # deploy everything
#   ./deploy/deploy_all.sh --dry-run  # print every gcloud command, touch nothing
#
# Prerequisites (checked below): gcloud authenticated, GOOGLE_CLOUD_PROJECT set,
# ClickHouse Cloud reachable and seeded (see README cloud verification step 0).

set -euo pipefail

# ---------------------------------------------------------------------------
# Config. Everything is read from the environment / .env, nothing hardcoded.
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env if present so the script works with the same config as `make demo`.
# .env is gitignored and never enters an image; here it is only a source of vars.
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GCP_REGION:-us-central1}"

API_SERVICE="cutpoint-api"
WATCHER_SERVICE="cutpoint-watcher"
EXTRACTOR_SERVICE="cutpoint-segment-extractor"

SCAN_TOPIC="cutpoint-retention-scan"
ANALYZE_TOPIC="cutpoint-analyze"
SCAN_SUB="cutpoint-retention-scan-push"
ANALYZE_SUB="cutpoint-analyze-push"
SCHEDULER_JOB="cutpoint-retention-scan-tick"
SCHEDULE="${CUTPOINT_SCAN_SCHEDULE:-*/15 * * * *}"

# Dedicated service accounts. The push SA mints the OIDC token that Pub/Sub
# attaches to each push request; the API and watcher verify it in api/auth.py.
PUSH_SA="cutpoint-pubsub-push@${PROJECT}.iam.gserviceaccount.com"
PUSH_SA_ID="cutpoint-pubsub-push"
RUNTIME_SA="cutpoint-runtime@${PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA_ID="cutpoint-runtime"

GCS_BUCKET="${GCS_BUCKET:-${PROJECT}-cutpoint-media}"

# ---------------------------------------------------------------------------
# run(): echo every command; execute it only when not in --dry-run.
# ---------------------------------------------------------------------------
run() {
    echo "+ $*"
    if [[ "$DRY_RUN" == "false" ]]; then
        "$@"
    fi
}

# exists(): true if a describe succeeds. Suppressed in dry-run so we still print
# the create commands rather than skipping them against a project we cannot read.
#
# NOTE ON IDEMPOTENCY. This script is idempotent by SKIPPING what already exists,
# which means a change to a resource's configuration never reaches a resource
# created by an earlier run. That is how the analyze subscription ended up
# without the dead-letter policy this script declares: the subscription already
# existed, so the create was skipped and the new flags never applied. Anything
# whose configuration matters is therefore re-applied with an explicit update
# below, not just created.
exists() {
    if [[ "$DRY_RUN" == "true" ]]; then
        return 1
    fi
    "$@" >/dev/null 2>&1
}

case "${CLICKHOUSE_HOST:-}" in
    ""|localhost|127.0.0.1|0.0.0.0)
        echo "ERROR: CLICKHOUSE_HOST is '${CLICKHOUSE_HOST:-<unset>}'." >&2
        echo "A Cloud Run container cannot reach your machine's localhost, so the" >&2
        echo "watcher would fail on every scan and the analyst on every request." >&2
        echo "Point CLICKHOUSE_HOST at a reachable instance (ClickHouse Cloud) first," >&2
        echo "or set CUTPOINT_ALLOW_LOCAL_CH=1 to deploy the plumbing anyway." >&2
        [[ "${CUTPOINT_ALLOW_LOCAL_CH:-}" == "1" ]] || exit 2
        echo "CUTPOINT_ALLOW_LOCAL_CH=1 set; continuing with an unreachable database." >&2
        ;;
esac

echo "=== CutPoint deploy ==="
echo "    project : ${PROJECT}"
echo "    region  : ${REGION}"
echo "    dry-run : ${DRY_RUN}"
echo

# ---------------------------------------------------------------------------
# 0. APIs. Enabling is free; only usage bills.
# ---------------------------------------------------------------------------
echo "--- enabling required APIs ---"
run gcloud services enable \
    run.googleapis.com \
    pubsub.googleapis.com \
    firestore.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    --project "${PROJECT}"

# ---------------------------------------------------------------------------
# 1. Service accounts
# ---------------------------------------------------------------------------
echo "--- service accounts ---"
if ! exists gcloud iam service-accounts describe "${PUSH_SA}" --project "${PROJECT}"; then
    run gcloud iam service-accounts create "${PUSH_SA_ID}" \
        --project "${PROJECT}" \
        --display-name "CutPoint Pub/Sub push identity"
fi
if ! exists gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT}"; then
    run gcloud iam service-accounts create "${RUNTIME_SA_ID}" \
        --project "${PROJECT}" \
        --display-name "CutPoint Cloud Run runtime identity"
fi

# Runtime SA needs: Firestore read/write, Vertex AI inference, GCS object rw,
# Pub/Sub publish (watcher -> cutpoint-analyze; api -> for /jobs), and invoke on
# the extractor (which is deployed --no-allow-unauthenticated).
echo "--- runtime SA roles ---"
for role in \
    roles/datastore.user \
    roles/aiplatform.user \
    roles/storage.objectAdmin \
    roles/pubsub.publisher \
    roles/run.invoker; do
    run gcloud projects add-iam-policy-binding "${PROJECT}" \
        --member "serviceAccount:${RUNTIME_SA}" \
        --role "${role}" \
        --condition None
done

# ---------------------------------------------------------------------------
# 2. Firestore (native mode). One database per project; create if absent.
# ---------------------------------------------------------------------------
echo "--- firestore ---"
if ! exists gcloud firestore databases describe --project "${PROJECT}" --database "(default)"; then
    run gcloud firestore databases create \
        --project "${PROJECT}" \
        --location "${REGION}" \
        --type firestore-native
fi

# ---------------------------------------------------------------------------
# 3. GCS bucket for rendered HTML and generated media
# ---------------------------------------------------------------------------
echo "--- gcs bucket ---"
if ! exists gcloud storage buckets describe "gs://${GCS_BUCKET}" --project "${PROJECT}"; then
    run gcloud storage buckets create "gs://${GCS_BUCKET}" \
        --project "${PROJECT}" \
        --location "${REGION}" \
        --uniform-bucket-level-access
fi

# ---------------------------------------------------------------------------
# 4. Pub/Sub topics
# ---------------------------------------------------------------------------
echo "--- pubsub topics ---"
# A dead-letter topic. Without one, a message the pipeline cannot process is
# redelivered until retention expires rather than being set aside, so one
# poisonous message becomes days of paid retries.
DEAD_TOPIC="cutpoint-dead-letter"
for topic in "${SCAN_TOPIC}" "${ANALYZE_TOPIC}" "${DEAD_TOPIC}"; do
    if ! exists gcloud pubsub topics describe "${topic}" --project "${PROJECT}"; then
        run gcloud pubsub topics create "${topic}" --project "${PROJECT}"
    fi
done

# ---------------------------------------------------------------------------
# 5. Cloud Run services (API + watcher share the root image via APP_MODULE).
#    The extractor keeps its own image; it is only (re)deployed by its own
#    script. Here we just resolve its URL to wire SEGMENT_EXTRACTOR_URL.
# ---------------------------------------------------------------------------
# Env values are joined with "@@" and passed with gcloud's "^@@^" delimiter
# syntax, so a value that itself contains a comma (CORS origins, the cron
# schedule) never gets mis-split into a bogus KEY=VALUE pair.
COMMON_ENV="GOOGLE_CLOUD_PROJECT=${PROJECT}"
COMMON_ENV="${COMMON_ENV}@@GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION:-global}"
COMMON_ENV="${COMMON_ENV}@@GOOGLE_GENAI_USE_VERTEXAI=TRUE"
COMMON_ENV="${COMMON_ENV}@@GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash}"
COMMON_ENV="${COMMON_ENV}@@CUTPOINT_STORE=firestore"
COMMON_ENV="${COMMON_ENV}@@CUTPOINT_ANALYZE_TOPIC=${ANALYZE_TOPIC}"
# Pinning CUTPOINT_AUDIENCE proves a token was minted FOR this service; it does
# not prove the caller is allowed to use it. Any attacker-controlled service
# account -- free to create in their own GCP project -- can mint a token for an
# arbitrary audience, so audience alone still let a stranger run the paid
# pipeline. The allowlist is what actually authorizes.
COMMON_ENV="${COMMON_ENV}@@CUTPOINT_ALLOWED_INVOKERS=${PUSH_SA},${RUNTIME_SA}"
COMMON_ENV="${COMMON_ENV}@@CUTPOINT_MAX_ANALYSES_PER_DAY=${CUTPOINT_MAX_ANALYSES_PER_DAY:-25}"
COMMON_ENV="${COMMON_ENV}@@CUTPOINT_MAX_CONCURRENT_PIPELINES=${CUTPOINT_MAX_CONCURRENT_PIPELINES:-2}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_PORT=${CLICKHOUSE_PORT:-8443}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_USER=${CLICKHOUSE_USER:-default}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_DATABASE=${CLICKHOUSE_DATABASE:-cutpoint}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_SECURE=${CLICKHOUSE_SECURE:-true}"
COMMON_ENV="${COMMON_ENV}@@CLICKHOUSE_VERIFY=${CLICKHOUSE_VERIFY:-true}"

# CLICKHOUSE_PASSWORD is a secret. Pass it separately and only if set, so it is
# never interpolated into a logged command line when empty. In production prefer
# --set-secrets with Secret Manager; kept as an env var here for a short-lived
# hackathon deploy.
SECRET_ENV=""
[[ -n "${CLICKHOUSE_PASSWORD:-}" ]] && SECRET_ENV="CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}"

echo "--- deploy ${API_SERVICE} ---"
API_ENV="${COMMON_ENV}@@APP_MODULE=api.main:app@@GCS_BUCKET=${GCS_BUCKET}"
API_ENV="${API_ENV}@@CUTPOINT_ALLOWED_ORIGINS=${CUTPOINT_ALLOWED_ORIGINS:-*}"
[[ -n "${SECRET_ENV}" ]] && API_ENV="${API_ENV}@@${SECRET_ENV}"
# --allow-unauthenticated is deliberate and is NOT the finding that was fixed.
# Platform-level auth rejects a request before any application code runs, which
# would also block GET /report and GET /trailers -- the endpoints the static UI
# must read with no credential. The paid endpoints are guarded in the
# application instead: POST /analyze, POST /jobs and POST /pubsub/analyze all
# require a verified Google-signed OIDC token (api/auth.py, fails closed), and
# concurrent pipelines are capped and shed with 429. The watcher below keeps
# platform-level auth because only Pub/Sub ever calls it.
run gcloud run deploy "${API_SERVICE}" \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --source "${REPO_ROOT}" \
    --service-account "${RUNTIME_SA}" \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --timeout 600 \
    --min-instances 0 \
    --max-instances 1 \
    --set-env-vars "^@@^${API_ENV}"

API_URL=""
if [[ "$DRY_RUN" == "false" ]]; then
    API_URL="$(gcloud run services describe "${API_SERVICE}" \
        --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)')"
    echo "    ${API_SERVICE} URL: ${API_URL}"
fi

# Wire the extractor URL into the API so the pipeline can reach it. The extractor
# must already be deployed (services/segment_extractor/deploy_cloud_run.sh).
EXTRACTOR_URL=""
if [[ "$DRY_RUN" == "false" ]]; then
    EXTRACTOR_URL="$(gcloud run services describe "${EXTRACTOR_SERVICE}" \
        --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)' 2>/dev/null || true)"
    if [[ -n "${EXTRACTOR_URL}" ]]; then
        run gcloud run services update "${API_SERVICE}" \
            --project "${PROJECT}" --region "${REGION}" \
            --update-env-vars "SEGMENT_EXTRACTOR_URL=${EXTRACTOR_URL}"
    else
        echo "    WARNING: ${EXTRACTOR_SERVICE} not found; deploy it first, then re-run."
    fi
fi

echo "--- deploy ${WATCHER_SERVICE} ---"
WATCHER_ENV="${COMMON_ENV}@@APP_MODULE=services.watcher.main:app"
[[ -n "${SECRET_ENV}" ]] && WATCHER_ENV="${WATCHER_ENV}@@${SECRET_ENV}"
run gcloud run deploy "${WATCHER_SERVICE}" \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --source "${REPO_ROOT}" \
    --service-account "${RUNTIME_SA}" \
    --no-allow-unauthenticated \
    --port 8080 \
    --memory 512Mi \
    --cpu 1 \
    --timeout 300 \
    --min-instances 0 \
    --max-instances 1 \
    --set-env-vars "^@@^${WATCHER_ENV}"

WATCHER_URL=""
if [[ "$DRY_RUN" == "false" ]]; then
    WATCHER_URL="$(gcloud run services describe "${WATCHER_SERVICE}" \
        --project "${PROJECT}" --region "${REGION}" --format 'value(status.url)')"
    echo "    ${WATCHER_SERVICE} URL: ${WATCHER_URL}"
fi

# The push SA must be allowed to invoke both private services.
echo "--- grant push SA invoke on api + watcher ---"
run gcloud run services add-iam-policy-binding "${API_SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" \
    --member "serviceAccount:${PUSH_SA}" --role roles/run.invoker
run gcloud run services add-iam-policy-binding "${WATCHER_SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" \
    --member "serviceAccount:${PUSH_SA}" --role roles/run.invoker

# ---------------------------------------------------------------------------
# 6. Push subscriptions. Each delivers to a private Cloud Run endpoint with an
#    OIDC token minted for the target's own URL as audience -- which is exactly
#    what api/auth.py verifies (CUTPOINT_AUDIENCE = the service URL).
# ---------------------------------------------------------------------------
# For the audience to match, the API/watcher need CUTPOINT_AUDIENCE = their URL.
if [[ "$DRY_RUN" == "false" && -n "${API_URL}" ]]; then
    run gcloud run services update "${API_SERVICE}" \
        --project "${PROJECT}" --region "${REGION}" \
        --update-env-vars "CUTPOINT_AUDIENCE=${API_URL}"
fi
if [[ "$DRY_RUN" == "false" && -n "${WATCHER_URL}" ]]; then
    run gcloud run services update "${WATCHER_SERVICE}" \
        --project "${PROJECT}" --region "${REGION}" \
        --update-env-vars "CUTPOINT_AUDIENCE=${WATCHER_URL}"
fi

echo "--- pubsub push subscriptions ---"
# retention-scan -> watcher /pubsub/scan
if ! exists gcloud pubsub subscriptions describe "${SCAN_SUB}" --project "${PROJECT}"; then
    run gcloud pubsub subscriptions create "${SCAN_SUB}" \
        --project "${PROJECT}" \
        --topic "${SCAN_TOPIC}" \
        --push-endpoint "${WATCHER_URL:-https://WATCHER_URL_PENDING}/pubsub/scan" \
        --push-auth-service-account "${PUSH_SA}" \
        --push-auth-token-audience "${WATCHER_URL:-https://WATCHER_URL_PENDING}" \
        --ack-deadline 120
fi

# analyze -> api /pubsub/analyze. ack-deadline 600 covers a full pipeline run;
# the handler runs inline and only acks on completion.
#
# Re-applied on every run, not only at creation, so the dead-letter policy
# reaches a subscription an earlier run already made.
if [[ "$DRY_RUN" == "false" ]] && gcloud pubsub subscriptions describe "${ANALYZE_SUB}" --project "${PROJECT}" >/dev/null 2>&1; then
    run gcloud pubsub subscriptions update "${ANALYZE_SUB}" \
        --project "${PROJECT}" \
        --dead-letter-topic "${DEAD_TOPIC}" \
        --max-delivery-attempts 5
fi

if ! exists gcloud pubsub subscriptions describe "${ANALYZE_SUB}" --project "${PROJECT}"; then
    run gcloud pubsub subscriptions create "${ANALYZE_SUB}" \
        --project "${PROJECT}" \
        --topic "${ANALYZE_TOPIC}" \
        --push-endpoint "${API_URL:-https://API_URL_PENDING}/pubsub/analyze" \
        --push-auth-service-account "${PUSH_SA}" \
        --push-auth-token-audience "${API_URL:-https://API_URL_PENDING}" \
        --ack-deadline 600 \
        --dead-letter-topic "${DEAD_TOPIC}" \
        --max-delivery-attempts 5
fi

# Pub/Sub needs permission to publish into the dead-letter topic and to ack the
# original subscription on its behalf.
PUBSUB_SA="service-$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)' 2>/dev/null)@gcp-sa-pubsub.iam.gserviceaccount.com"
if [[ "$DRY_RUN" == "false" ]]; then
    run gcloud pubsub topics add-iam-policy-binding "${DEAD_TOPIC}" \
        --project "${PROJECT}" --member "serviceAccount:${PUBSUB_SA}" --role roles/pubsub.publisher
    run gcloud pubsub subscriptions add-iam-policy-binding "${ANALYZE_SUB}" \
        --project "${PROJECT}" --member "serviceAccount:${PUBSUB_SA}" --role roles/pubsub.subscriber
fi

# ---------------------------------------------------------------------------
# 7. Cloud Scheduler tick -> retention-scan topic
# ---------------------------------------------------------------------------
echo "--- cloud scheduler ---"
if ! exists gcloud scheduler jobs describe "${SCHEDULER_JOB}" --project "${PROJECT}" --location "${REGION}"; then
    run gcloud scheduler jobs create pubsub "${SCHEDULER_JOB}" \
        --project "${PROJECT}" \
        --location "${REGION}" \
        --schedule "${SCHEDULE}" \
        --topic "${SCAN_TOPIC}" \
        --message-body '{"trigger":"scheduled-scan"}'
fi

# Created PAUSED on purpose. An enabled */15 tick wakes the watcher 96 times a
# day forever, and every wake is a Cloud Run start plus a ClickHouse query --
# spend with no one watching. Resume it only for a live demonstration:
#   gcloud scheduler jobs resume ${SCHEDULER_JOB} --location ${REGION}
# Set CUTPOINT_ENABLE_SCHEDULE=1 to leave it running after deploy.
if [[ "${CUTPOINT_ENABLE_SCHEDULE:-}" == "1" ]]; then
    run gcloud scheduler jobs resume "${SCHEDULER_JOB}" --project "${PROJECT}" --location "${REGION}"
else
    run gcloud scheduler jobs pause "${SCHEDULER_JOB}" --project "${PROJECT}" --location "${REGION}"
fi

echo
echo "=== deploy complete ==="
if [[ "$DRY_RUN" == "false" ]]; then
    echo "    API     : ${API_URL}"
    echo "    Watcher : ${WATCHER_URL}"
    echo "    Bucket  : gs://${GCS_BUCKET}"
    echo
    echo "Manual verification (see README cloud steps):"
    echo "  gcloud pubsub topics publish ${SCAN_TOPIC} --message '{}' --project ${PROJECT}"
    echo "  then check watcher logs for a fingerprint diff."
else
    echo "    dry-run: nothing was created. Re-run without --dry-run to deploy."
fi
