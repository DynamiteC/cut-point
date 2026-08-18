"""Batch loader: reads data/events/{trailer_id}.ndjson and data/ground_truth.json,
inserts into cutpoint.raw_playback_events and cutpoint.trailers via clickhouse-connect.

Idempotent via --force (truncate-then-load) and a checkpoint file that records how
many rows of each trailer file have already been inserted, so a re-run without
--force resumes rather than duplicating rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ingest.clickhouse_client import get_ingest_client

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
BATCH_SIZE = 50_000

EVENT_COLUMNS = [
    "event_ts",
    "trailer_id",
    "session_id",
    "cohort",
    "region",
    "device",
    "second_offset",
    "event_type",
]


def checkpoint_path(trailer_id: str) -> Path:
    return DATA_DIR / f".checkpoint_{trailer_id}.json"


def load_checkpoint(trailer_id: str) -> int:
    path = checkpoint_path(trailer_id)
    if not path.exists():
        return 0
    return json.loads(path.read_text()).get("rows_loaded", 0)


def save_checkpoint(trailer_id: str, rows_loaded: int) -> None:
    checkpoint_path(trailer_id).write_text(json.dumps({"rows_loaded": rows_loaded}))


def event_to_row(evt: dict) -> tuple:
    return (
        datetime.fromtimestamp(evt["event_ts"], tz=timezone.utc),
        evt["trailer_id"],
        evt["session_id"],
        evt["cohort"],
        evt["region"],
        evt["device"],
        evt["second_offset"],
        evt["event_type"],
    )


def load_trailer_events(client, trailer_id: str, force: bool) -> int:
    ndjson_path = DATA_DIR / "events" / f"{trailer_id}.ndjson"
    if not ndjson_path.exists():
        print(f"skip {trailer_id}: no events file at {ndjson_path} (run make generate-data first)")
        return 0

    skip_rows = 0 if force else load_checkpoint(trailer_id)
    total_inserted = 0
    batch: list[tuple] = []

    with ndjson_path.open() as fh:
        for line_no, line in enumerate(fh):
            if line_no < skip_rows:
                continue
            batch.append(event_to_row(json.loads(line)))
            if len(batch) >= BATCH_SIZE:
                client.insert("raw_playback_events", batch, column_names=EVENT_COLUMNS)
                total_inserted += len(batch)
                save_checkpoint(trailer_id, skip_rows + total_inserted)
                batch = []

    if batch:
        client.insert("raw_playback_events", batch, column_names=EVENT_COLUMNS)
        total_inserted += len(batch)
        save_checkpoint(trailer_id, skip_rows + total_inserted)

    return total_inserted


def load_trailers_table(client, trailer_ids: list[str], ground_truth: dict) -> int:
    rows = []
    for trailer_id in trailer_ids:
        duration_s = ground_truth.get(trailer_id, {}).get("duration_s", 0)
        rows.append(
            (
                trailer_id,
                trailer_id.replace("_", " ").title(),
                duration_s,
                f"data/videos/{trailer_id}.mp4",
            )
        )
    client.insert("trailers", rows, column_names=["trailer_id", "title", "duration_s", "video_path"])
    return len(rows)


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Load synthetic events into ClickHouse")
    parser.add_argument("--force", action="store_true", help="truncate tables before loading")
    args = parser.parse_args()

    client = get_ingest_client()

    if args.force:
        client.command("TRUNCATE TABLE IF EXISTS raw_playback_events")
        client.command("TRUNCATE TABLE IF EXISTS trailers")
        for checkpoint in DATA_DIR.glob(".checkpoint_*.json"):
            checkpoint.unlink()

    ground_truth_path = DATA_DIR / "ground_truth.json"
    if not ground_truth_path.exists():
        print("no ground_truth.json found -- run make generate-data first")
        return 1
    ground_truth = json.loads(ground_truth_path.read_text())

    trailer_ids = list(ground_truth.keys())
    load_trailers_table(client, trailer_ids, ground_truth)

    for trailer_id in trailer_ids:
        n = load_trailer_events(client, trailer_id, args.force)
        print(f"{trailer_id}: inserted {n} event rows")

    for table in ["raw_playback_events", "trailers"]:
        count = client.command(f"SELECT count() FROM {table}")
        print(f"row count {table}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
