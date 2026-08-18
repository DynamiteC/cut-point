"""Phase 1 gate: assert row counts > 0 for all tables and that the injected
ground-truth cliffs are actually present in the loaded data.

For each cliff, checks that (viewers at second-1) - (viewers at second+1), summed
over the cliff's affected cohorts, is at least drop_pct * 0.6 of the second-1 value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from ingest.clickhouse_client import get_ingest_client

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

TABLES = ["raw_playback_events", "trailers"]


def viewers_at_second(client, trailer_id: str, cohorts: list[str], second: int) -> int:
    cohort_list = ",".join(f"'{c}'" for c in cohorts)
    query = f"""
        SELECT uniqExact(session_id)
        FROM raw_playback_events
        WHERE trailer_id = %(trailer_id)s
          AND cohort IN ({cohort_list})
          AND second_offset = %(second)s
          AND event_type = 'heartbeat'
    """
    return client.command(query, parameters={"trailer_id": trailer_id, "second": second})


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    client = get_ingest_client()

    failures: list[str] = []

    for table in TABLES:
        count = client.command(f"SELECT count() FROM {table}")
        print(f"{table}: {count} rows")
        if count == 0:
            failures.append(f"{table} has 0 rows")

    ground_truth_path = DATA_DIR / "ground_truth.json"
    if not ground_truth_path.exists():
        print("no ground_truth.json found -- run make generate-data first")
        return 1
    ground_truth = json.loads(ground_truth_path.read_text())

    for trailer_id, spec in ground_truth.items():
        for cliff in spec["cliffs"]:
            second = cliff["second"]
            cohorts = cliff["cohorts"]
            drop_pct = cliff["drop_pct"]

            before = viewers_at_second(client, trailer_id, cohorts, max(0, second - 1))
            after = viewers_at_second(client, trailer_id, cohorts, second + 1)

            if before == 0:
                failures.append(f"{trailer_id} second {second}: no viewers before cliff (before=0)")
                continue

            observed_drop = (before - after) / before
            required_drop = drop_pct * 0.6
            status = "OK" if observed_drop >= required_drop else "FAIL"
            print(
                f"{trailer_id} second {second} cohorts={cohorts}: "
                f"before={before} after={after} observed_drop={observed_drop:.3f} "
                f"required>={required_drop:.3f} [{status}]"
            )
            if observed_drop < required_drop:
                failures.append(
                    f"{trailer_id} second {second}: observed_drop {observed_drop:.3f} "
                    f"< required {required_drop:.3f}"
                )

    if failures:
        print("\nVERIFY-DATA FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nVERIFY-DATA PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
