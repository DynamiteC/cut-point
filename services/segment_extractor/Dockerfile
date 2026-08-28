FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pinned to the versions resolved in uv.lock. Unpinned installs here drifted from
# the lockfile the rest of the project builds against.
RUN pip install --no-cache-dir \
    fastapi==0.141.1 \
    uvicorn==0.52.3 \
    pydantic==2.13.4 \
    google-cloud-storage==2.19.0

# Build context is this directory. main.py resolves REPO_ROOT as
# parent.parent.parent, so it must land at /app/services/segment_extractor/.
COPY . ./services/segment_extractor/

EXPOSE 8081
CMD ["sh", "-c", "uvicorn services.segment_extractor.main:app --host 0.0.0.0 --port ${PORT:-8081}"]
