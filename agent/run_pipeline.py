"""CLI driver for the CutPoint agent pipeline.

Usage:
    python -m agent.run_pipeline --trailer demo_001 --dry-run
    python -m agent.run_pipeline --trailer demo_001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

PIPELINE_STEPS = ["analyst", "extractor", "diagnostician", "reporter"]


def print_dry_run_plan(trailer_id: str) -> None:
    print(f"CutPoint pipeline plan for trailer_id={trailer_id} (dry run, no cloud calls):")
    for i, step in enumerate(PIPELINE_STEPS, start=1):
        print(f"  {i}. {step}")
    print("\nresolved 4-step plan. no ClickHouse, Gemini, or extractor calls were made.")


async def run_live(trailer_id: str) -> dict:
    import json as _json

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from agent.cutpoint_agent.agent import build_root_agent

    ground_truth_path = REPO_ROOT / "data" / "ground_truth.json"
    ground_truth = _json.loads(ground_truth_path.read_text())
    if trailer_id not in ground_truth:
        raise SystemExit(f"unknown trailer_id {trailer_id}; known: {list(ground_truth)}")

    duration_s = ground_truth[trailer_id]["duration_s"]
    video_path = str(REPO_ROOT / "data" / "videos" / f"{trailer_id}.mp4")

    root_agent = build_root_agent()
    runner = InMemoryRunner(agent=root_agent, app_name="cutpoint")
    session = await runner.session_service.create_session(
        app_name="cutpoint", user_id="cli", state={
            "video_path": video_path,
            "title": trailer_id.replace("_", " ").title(),
            "duration_s": duration_s,
        }
    )

    final_state = {}
    async for event in runner.run_async(
        user_id="cli",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=f"trailer_id={trailer_id}")]),
    ):
        if event.actions and event.actions.state_delta:
            final_state.update(event.actions.state_delta)

    return final_state


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run the CutPoint agent pipeline")
    parser.add_argument("--trailer", required=True, help="trailer_id, e.g. demo_001")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without cloud calls")
    args = parser.parse_args()

    if args.dry_run:
        print_dry_run_plan(args.trailer)
        return 0

    final_state = asyncio.run(run_live(args.trailer))
    report_path = final_state.get("report_path")
    if report_path:
        print(f"report written to {report_path}")
        notes = final_state.get("directors_notes", {})
        for cliff in notes.get("cliffs", []):
            print(f"  second {cliff['second']}: {cliff['hypothesis']}")
    else:
        print("pipeline did not produce a report_path -- see state above")
        print(json.dumps(final_state, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
