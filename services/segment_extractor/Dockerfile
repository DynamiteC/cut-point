FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir fastapi uvicorn pydantic

COPY services/segment_extractor ./services/segment_extractor

EXPOSE 8081
CMD ["uvicorn", "services.segment_extractor.main:app", "--host", "0.0.0.0", "--port", "8081"]
