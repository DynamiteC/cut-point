"""make demo: generate (if absent) -> load -> start extractor -> run pipeline
live for demo_001 -> write reports -> print a one-line summary per cliff.

Per TASK.md rule 7, if live ClickHouse or live Vertex AI credentials are missing,
this fails loudly with an actionable MissingCredentialError rather than
substituting a mock -- mocks belong only in tests/.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Overridable so every trailer in ground_truth.json can be demoed, not just the
# first one: `python scripts/run_demo.py demo_002`.
TRAILER_ID = sys.argv[1] if len(sys.argv) > 1 else "demo_001"
EXTRACTOR_PORT = 8081


def ensure_data_generated() -> None:
    ground_truth = REPO_ROOT / "data" / "ground_truth.json"
    if not ground_truth.exists():
        print("no data found -- generating synthetic events...")
        subprocess.run([sys.executable, "-m", "ingest.generate", "--seed", "42"], check=True, cwd=REPO_ROOT)
        subprocess.run([sys.executable, "-m", "ingest.load"], check=True, cwd=REPO_ROOT)
    else:
        print("data/ground_truth.json already present, skipping generate+load")


def start_extractor_service() -> subprocess.Popen:
    print(f"starting segment extractor on :{EXTRACTOR_PORT} ...")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.segment_extractor.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(EXTRACTOR_PORT),
        ],
        cwd=REPO_ROOT,
    )
    for _ in range(30):
        try:
            response = httpx.get(f"http://127.0.0.1:{EXTRACTOR_PORT}/health", timeout=1)
            if response.status_code == 200:
                print("extractor service is up")
                return proc
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("segment extractor service did not become healthy in time")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    # A trailer with no injected cliffs (demo_control) never extracts a clip, so
    # it needs no source video. Everything else does.
    video_path = REPO_ROOT / "data" / "videos" / f"{TRAILER_ID}.mp4"
    if not video_path.exists():
        import json as _json
        gt = _json.loads((REPO_ROOT / "data" / "ground_truth.json").read_text())
        if gt.get(TRAILER_ID, {}).get("cliffs"):
            print(f"no sample video at {video_path} -- "
                  f"run scripts/fetch_sample_video.sh {TRAILER_ID} first")
            return 1
        print(f"{TRAILER_ID} has no injected cliffs; proceeding without a source video")

    ensure_data_generated()
    extractor_proc = start_extractor_service()

    try:
        import asyncio

        from google.auth.exceptions import DefaultCredentialsError

        from agent.run_pipeline import run_live

        try:
            final_state = asyncio.run(run_live(TRAILER_ID))
        except DefaultCredentialsError as exc:
            print(
                "\nMissingCredentialError: no Google Cloud Application Default "
                "Credentials found -- run `gcloud auth application-default login` "
                "and set GOOGLE_CLOUD_PROJECT in .env -- see README section Setup"
            )
            raise SystemExit(1) from exc
    finally:
        extractor_proc.terminate()
        extractor_proc.wait(timeout=10)

    report_path = final_state.get("report_path")
    if not report_path:
        print("demo did not produce a report_path")
        return 1

    print(f"\nreport written to {report_path}")
    notes = final_state.get("directors_notes", {})
    for cliff in notes.get("cliffs", []):
        print(f"  second {cliff['second']}: {cliff['hypothesis']}")

    from report.render import render_report_from_json

    md_path, html_path = render_report_from_json(Path(report_path))
    print(f"markdown: {md_path}")
    print(f"html: {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
