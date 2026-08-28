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
    --max-instances 1)

echo "Would run: ${CMD[*]}"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "dry-run: not deploying"
    exit 0
fi

"${CMD[@]}"
