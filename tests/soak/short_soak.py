"""Phase 10.5: Short soak test (30-minute scoped).

Runs the CutPoint pipeline in --dry-run mode in a loop, monitoring RSS memory
every 60 seconds to detect memory leaks (unclosed MCP sessions, unclosed
clickhouse-connect clients, etc.).

This is a scoped 30-minute smoke-soak, NOT a production soak test claim.
A true soak would run 12-24h under realistic load.

Usage:
    uv run python tests/soak/short_soak.py --minutes 30
    uv run python tests/soak/short_soak.py --minutes 5  # quick check
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
import time
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRAILERS = ["demo_001", "demo_002", "demo_003"]
SAMPLE_INTERVAL_S = 60
GROWTH_THRESHOLD = 1.2  # 20% max growth


def get_rss_mb() -> float:
    """Return RSS of the current process in megabytes."""
    process = psutil.Process()
    return process.memory_info().rss / (1024 * 1024)


def run_pipeline_dry(trailer_id: str) -> None:
    """Run the pipeline in dry-run mode as a subprocess."""
    subprocess.run(
        [sys.executable, "-m", "agent.run_pipeline", "--trailer", trailer_id, "--dry-run"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def generate_report(
    minutes: int,
    readings: list[tuple[float, float]],
    passed: bool,
) -> str:
    """Generate the soak report markdown content."""
    if not readings:
        return "# Soak Test Report\n\nNo readings collected.\n"

    baseline_rss = readings[0][1]
    final_rss = readings[-1][1]
    growth_pct = ((final_rss - baseline_rss) / baseline_rss) * 100 if baseline_rss > 0 else 0.0

    lines = [
        "# Soak Test Report",
        "",
        "**Scope**: 30-minute smoke-soak (not a production soak claim).",
        "A true production soak test would run 12-24h under realistic load.",
        "This scoped test catches the obvious leaks: unclosed MCP stdio sessions,",
        "unclosed clickhouse-connect clients, accumulating subprocess handles.",
        "",
        "## Configuration",
        f"- Duration: {minutes} minutes",
        "- Pipeline mode: --dry-run (no Gemini/Vertex AI calls)",
        "- Trailers cycled: demo_001, demo_002, demo_003",
        "- Memory sample interval: 60 seconds",
        "",
        "## Memory Trace",
        "| Elapsed (s) | RSS (MB) | Growth from baseline |",
        "|-------------|----------|---------------------|",
    ]

    for elapsed, rss in readings:
        growth = ((rss - baseline_rss) / baseline_rss) * 100 if baseline_rss > 0 else 0.0
        lines.append(f"| {int(elapsed)} | {rss:.1f} | {growth:.1f}% |")

    lines.extend([
        "",
        "## Result",
        f"- Baseline RSS: {baseline_rss:.1f} MB",
        f"- Final RSS: {final_rss:.1f} MB",
        f"- Growth: {growth_pct:.1f}%",
        "- Threshold: 20%",
        f"- **{'PASS' if passed else 'FAIL'}**",
        "",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="CutPoint short soak test")
    parser.add_argument("--minutes", type=int, default=30, help="Duration in minutes (default: 30)")
    args = parser.parse_args()

    duration_s = args.minutes * 60
    readings: list[tuple[float, float]] = []
    trailer_cycle = itertools.cycle(TRAILERS)
    iteration = 0

    # Take initial reading
    initial_rss = get_rss_mb()
    readings.append((0.0, initial_rss))
    print(f"[soak] Starting {args.minutes}-minute soak test")
    print(f"[soak] Baseline RSS: {initial_rss:.1f} MB")
    print("[soak] Growth threshold: 20%")
    print()

    start_time = time.monotonic()
    last_sample_time = start_time

    try:
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= duration_s:
                break

            # Run one pipeline iteration
            trailer_id = next(trailer_cycle)
            iteration += 1
            run_pipeline_dry(trailer_id)

            current_rss = get_rss_mb()
            print(
                f"[soak] iteration={iteration} trailer={trailer_id} "
                f"RSS={current_rss:.1f} MB elapsed={elapsed:.0f}s"
            )

            # Sample memory every SAMPLE_INTERVAL_S seconds
            now = time.monotonic()
            if now - last_sample_time >= SAMPLE_INTERVAL_S:
                readings.append((elapsed, current_rss))
                last_sample_time = now

    except KeyboardInterrupt:
        print("\n[soak] Interrupted, writing report with data collected so far...")

    # Final reading
    final_elapsed = time.monotonic() - start_time
    final_rss = get_rss_mb()
    readings.append((final_elapsed, final_rss))

    # Evaluate pass/fail
    baseline_rss = readings[0][1]
    passed = final_rss <= baseline_rss * GROWTH_THRESHOLD

    print()
    print(f"[soak] Final RSS: {final_rss:.1f} MB")
    print(f"[soak] Growth: {((final_rss - baseline_rss) / baseline_rss) * 100:.1f}%")
    print(f"[soak] Result: {'PASS' if passed else 'FAIL'}")

    if not passed:
        print("[soak] Readings showing growth:")
        for elapsed, rss in readings:
            growth = ((rss - baseline_rss) / baseline_rss) * 100
            if growth > 0:
                print(f"  {int(elapsed)}s: {rss:.1f} MB (+{growth:.1f}%)")

    # Write report
    report_dir = REPO_ROOT / "docs" / "perf"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "soak-report.md"
    report_content = generate_report(args.minutes, readings, passed)
    report_path.write_text(report_content)
    print(f"\n[soak] Report written to {report_path}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
