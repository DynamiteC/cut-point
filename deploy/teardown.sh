#!/usr/bin/env bash
#
# Tear down the CutPoint async control plane. Run this after judging so the
# project stops billing. The $150 credit form closed, so idle resources are
# real money -- even at --min-instances=0, subscriptions and the scheduler keep
# waking services up.
#
# Deletes: Cloud Scheduler job, push subscriptions, Pub/Sub topics, and the
# three Cloud Run services (api, watcher, extractor).
#
# Does NOT delete by default: the Firestore database (holds the judged reports
# and job history) and the GCS bucket (holds rendered HTML and generated media).
# Pass --purge-data to remove those too.
#
# Usage:
#   ./deploy/teardown.sh              # stop compute + messaging, keep data
#   ./deploy/teardown.sh --dry-run    # print deletions, touch nothing
#   ./deploy/teardown.sh --purge-data # also delete Firestore data + GCS bucket

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

DRY_RUN=false
PURGE_DATA=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --purge-data) PURGE_DATA=true ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GCP_REGION:-us-central1}"
GCS_BUCKET="${GCS_BUCKET:-${PROJECT}-cutpoint-media}"

SCHEDULER_JOB="cutpoint-retention-scan-tick"
SCAN_SUB="cutpoint-retention-scan-push"
ANALYZE_SUB="cutpoint-analyze-push"
SCAN_TOPIC="cutpoint-retention-scan"
ANALYZE_TOPIC="cutpoint-analyze"

# Delete the compute-and-messaging plane first (this is what actually bills),
# then optionally the data.
run() {
    echo "+ $*"
    if [[ "$DRY_RUN" == "false" ]]; then
        # A missing resource is not an error during teardown -- we want the
        # script to be safe to re-run and to tolerate partial prior deploys.
        "$@" || echo "    (already gone or not deletable, continuing)"
    fi
}

echo "=== CutPoint teardown ==="
echo "    project    : ${PROJECT}"
echo "    region     : ${REGION}"
echo "    dry-run    : ${DRY_RUN}"
echo "    purge-data : ${PURGE_DATA}"
echo

echo "--- scheduler ---"
run gcloud scheduler jobs delete "${SCHEDULER_JOB}" \
    --project "${PROJECT}" --location "${REGION}" --quiet

echo "--- subscriptions ---"
run gcloud pubsub subscriptions delete "${SCAN_SUB}" --project "${PROJECT}" --quiet
run gcloud pubsub subscriptions delete "${ANALYZE_SUB}" --project "${PROJECT}" --quiet

echo "--- topics ---"
run gcloud pubsub topics delete "${SCAN_TOPIC}" --project "${PROJECT}" --quiet
run gcloud pubsub topics delete "${ANALYZE_TOPIC}" --project "${PROJECT}" --quiet

echo "--- cloud run services ---"
for svc in cutpoint-api cutpoint-watcher cutpoint-segment-extractor; do
    run gcloud run services delete "${svc}" \
        --project "${PROJECT}" --region "${REGION}" --quiet
done

if [[ "$PURGE_DATA" == "true" ]]; then
    echo "--- PURGING DATA (Firestore + GCS) ---"
    # Firestore has no single "delete database" for the default DB via gcloud in
    # all releases; delete the collections instead. This removes judged reports.
    for coll in cutpoint_reports cutpoint_jobs cutpoint_watch; do
        run gcloud firestore documents delete --all-collection-ids "${coll}" \
            --project "${PROJECT}" --quiet 2>/dev/null || \
            echo "    (delete ${coll} manually if this gcloud lacks the command)"
    done
    run gcloud storage rm --recursive "gs://${GCS_BUCKET}" --project "${PROJECT}"
else
    echo "--- data kept (Firestore + gs://${GCS_BUCKET}). Use --purge-data to remove. ---"
fi

echo
echo "=== teardown complete ==="
if [[ "$DRY_RUN" == "true" ]]; then
    echo "    dry-run: nothing was deleted."
fi
