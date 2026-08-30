#!/usr/bin/env bash
# Deploy the segment extractor service to Cloud Run.
#
# Deliberately --no-allow-unauthenticated: this endpoint shells out to ffmpeg, so a
# public one is a free transcoding farm for anyone who finds it. The API service
# account is granted roles/run.invoker instead (see deploy/deploy_all.sh).
# Usage: ./deploy_cloud_run.sh [--dry-run]
set -euo pipefail

SERVICE_NAME="cutpoint-segment-extractor"
REGION="${GCP_REGION:-us-central1}"
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"

# The service ran as the default compute account with no GCS_BUCKET, so it had
# broader permissions than it needs and no bucket to read a source video from or
# write a clip to. Both are set here now: the same runtime identity the rest of
# the plane uses, and the bucket its reads are confined to.
RUNTIME_SA="${RUNTIME_SA:-cutpoint-runtime@${PROJECT}.iam.gserviceaccount.com}"
GCS_BUCKET="${GCS_BUCKET:-${PROJECT}-cutpoint-media}"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

CMD=(gcloud run deploy "$SERVICE_NAME" \
    --project "$PROJECT" \
    --region "$REGION" \
    --source "$(dirname "$0")" \
    --no-allow-unauthenticated \
    --port 8081 \
    --memory 512Mi \
    --min-instances 0 \
    --max-instances 1 \
    --service-account "$RUNTIME_SA" \
    --set-env-vars "GCS_BUCKET=${GCS_BUCKET}")

echo "Would run: ${CMD[*]}"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "dry-run: not deploying"
    exit 0
fi

"${CMD[@]}"
