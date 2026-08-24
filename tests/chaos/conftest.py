"""Chaos test conftest: collects test results and generates docs/perf/chaos-report.md."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "docs" / "perf" / "chaos-report.md"

# Maps test nodeid to expected failure mode description
SCENARIO_META: dict[str, dict[str, str]] = {
    "test_chaos_extractor_kill": {
        "scenario": "Extractor kill mid-pipeline",
        "expected": "Clear error, no partial report",
    },
    "test_chaos_clickhouse_port": {
        "scenario": "Wrong ClickHouse port",
        "expected": "Actionable error within timeout",
    },
    "test_chaos_corrupt_video": {
        "scenario": "Corrupt video file",
        "expected": "Per-clip error, pipeline continues",
    },
    "test_chaos_gemini_timeout": {
        "scenario": "Gemini API timeout",
        "expected": "Bounded retries, blast-radius containment",
    },
}

_results: list[dict[str, str]] = []


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        # Match the test module name to scenario metadata
        module_name = Path(item.fspath).stem
        meta = SCENARIO_META.get(module_name)
        if meta:
            _results.append({
                "scenario": meta["scenario"],
                "expected": meta["expected"],
                "actual": (
                    "Passed as expected"
                    if report.passed
                    else f"FAILED: {report.longreprtext[:120]}"
                ),
                "result": "PASS" if report.passed else "FAIL",
            })


def pytest_sessionfinish(session, exitstatus):
    if not _results:
        return
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Deduplicate: keep one row per scenario (first failure wins, otherwise first pass)
    seen: dict[str, dict[str, str]] = {}
    for row in _results:
        key = row["scenario"]
        if key not in seen or row["result"] == "FAIL" and seen[key]["result"] == "PASS":
            seen[key] = row

    lines = [
        "# Chaos Test Report\n",
        "",
        "| Scenario | Expected Failure Mode | Actual Behavior | Result |",
        "|----------|----------------------|-----------------|--------|",
    ]
    for row in seen.values():
        lines.append(
            f"| {row['scenario']} | {row['expected']} | {row['actual']} | {row['result']} |"
        )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
