"""Synthetic trailer playback event generator with known injected retention cliffs.

Ground truth is the test oracle: every injected cliff is recorded in
data/ground_truth.json so tests/test_detector.py can assert the detector recovers
each one within +/-2 seconds.

Streams NDJSON per trailer to data/events/{trailer_id}.ndjson -- never holds the
full dataset in memory.
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

COHORTS = ["13-17", "18-24", "25-34", "35-44", "45+"]
COHORT_WEIGHTS = [0.12, 0.30, 0.28, 0.18, 0.12]
REGIONS = ["us", "eu", "apac", "latam", "mea"]
REGION_WEIGHTS = [0.40, 0.25, 0.20, 0.10, 0.05]
DEVICES = ["mobile", "desktop", "tv"]
DEVICE_WEIGHTS = [0.55, 0.30, 0.15]

TRAILER_SPECS = {
    "demo_001": {"duration_s": 90, "sessions": 50_000},
    "demo_002": {"duration_s": 120, "sessions": 55_000},
    "demo_003": {"duration_s": 75, "sessions": 45_000},
    # Control trailer: NO injected cliffs, pure baseline decay + noise. Exists so
    # tests/test_detector.py can assert a genuine, non-circular false-positive
    # rate on data the detector's thresholds were never tuned against (see
    # PROGRESS.md "Post-phase-9 hardening" for why this matters).
    "demo_control": {"duration_s": 60, "sessions": 40_000},
}

# Injected ground-truth cliffs: elevated exit probability at `second` for `cohorts`.
# demo_control intentionally has no entry -- it is the false-positive control group.
INJECTED_CLIFFS = {
    "demo_001": [
        {"second": 22, "drop_pct": 0.18, "cohorts": ["13-17", "18-24"]},
        {"second": 47, "drop_pct": 0.22, "cohorts": ["18-24", "25-34"]},
        {"second": 68, "drop_pct": 0.15, "cohorts": ["35-44", "45+"]},
    ],
    "demo_002": [
        {"second": 30, "drop_pct": 0.20, "cohorts": ["18-24"]},
        {"second": 75, "drop_pct": 0.25, "cohorts": ["25-34", "35-44"]},
    ],
    "demo_003": [
        {"second": 15, "drop_pct": 0.17, "cohorts": ["13-17", "18-24", "25-34"]},
        {"second": 50, "drop_pct": 0.19, "cohorts": ["45+"]},
    ],
    "demo_control": [],
}

BASE_EXIT_HAZARD = 0.012  # per-second baseline exit probability, tuned for ~35% end retention


@dataclass
class SessionState:
    session_id: str
    cohort: str
    region: str
    device: str
    events: list[dict] = field(default_factory=list)


def weighted_choice(rng: random.Random, options: list[str], weights: list[float]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def cliff_hazard_bonus(second: int, cohort: str, cliffs: list[dict]) -> float:
    bonus = 0.0
    for cliff in cliffs:
        if second == cliff["second"] and cohort in cliff["cohorts"]:
            # Convert a target drop_pct at this second into an elevated one-shot hazard.
            bonus += cliff["drop_pct"]
    return bonus


def simulate_session(
    rng: random.Random,
    trailer_id: str,
    duration_s: int,
    cliffs: list[dict],
    base_ts_ms: int,
) -> list[dict]:
    session_id = str(uuid.uuid4())
    cohort = weighted_choice(rng, COHORTS, COHORT_WEIGHTS)
    region = weighted_choice(rng, REGIONS, REGION_WEIGHTS)
    device = weighted_choice(rng, DEVICES, DEVICE_WEIGHTS)

    events: list[dict] = []
    ts_ms = base_ts_ms + rng.randint(0, 6 * 24 * 3600 * 1000)  # spread over 7-day window

    def event(second_offset: int, event_type: str) -> dict:
        return {
            "event_ts": (ts_ms + second_offset * 1000) / 1000.0,
            "trailer_id": trailer_id,
            "session_id": session_id,
            "cohort": cohort,
            "region": region,
            "device": device,
            "second_offset": second_offset,
            "event_type": event_type,
        }

    events.append(event(0, "start"))
    for second in range(duration_s):
        events.append(event(second, "heartbeat"))
        hazard = BASE_EXIT_HAZARD + cliff_hazard_bonus(second, cohort, cliffs)
        if rng.random() < hazard:
            events.append(event(second, "exit"))
            return events
    events.append(event(duration_s - 1, "complete"))
    return events


def generate_trailer(trailer_id: str, spec: dict, seed: int) -> int:
    rng = random.Random(f"{seed}:{trailer_id}")
    cliffs = INJECTED_CLIFFS.get(trailer_id, [])
    duration_s = spec["duration_s"]
    n_sessions = spec["sessions"]

    out_path = DATA_DIR / "events" / f"{trailer_id}.ndjson"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    base_ts_ms = 1_700_000_000_000  # fixed epoch anchor for determinism
    row_count = 0
    with out_path.open("w") as fh:
        for _ in range(n_sessions):
            for evt in simulate_session(rng, trailer_id, duration_s, cliffs, base_ts_ms):
                fh.write(json.dumps(evt) + "\n")
                row_count += 1
    return row_count


def write_ground_truth() -> None:
    ground_truth = {
        trailer_id: {
            "duration_s": TRAILER_SPECS[trailer_id]["duration_s"],
            "cliffs": cliffs,
        }
        for trailer_id, cliffs in INJECTED_CLIFFS.items()
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic CutPoint playback events")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sessions", type=int, default=None, help="override sessions per trailer")
    parser.add_argument(
        "--trailers", nargs="*", default=list(TRAILER_SPECS.keys()), help="subset of trailer ids"
    )
    args = parser.parse_args()

    write_ground_truth()

    for trailer_id in args.trailers:
        spec = dict(TRAILER_SPECS[trailer_id])
        if args.sessions:
            spec["sessions"] = args.sessions
        row_count = generate_trailer(trailer_id, spec, args.seed)
        print(f"{trailer_id}: wrote {row_count} events ({spec['sessions']} sessions)")

    print("ground truth written to data/ground_truth.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
