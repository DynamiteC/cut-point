"""Load report generator: reads load test results and produces markdown + chart.

Reads:
  tests/load/results/ingest_results.json
  tests/load/results/api_results.json

Produces:
  docs/perf/load-report.md   (formatted tables)
  docs/perf/load-chart.png   (latency-vs-concurrency line chart)

Usage:
  uv run python scripts/load_report.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "tests" / "load" / "results"
PERF_DIR = REPO_ROOT / "docs" / "perf"

INGEST_RESULTS = RESULTS_DIR / "ingest_results.json"
API_RESULTS = RESULTS_DIR / "api_results.json"


def load_results() -> tuple[dict | None, dict | None]:
    """Load result JSON files. Returns (ingest_data, api_data) or exits on missing files."""
    missing = []
    if not INGEST_RESULTS.exists():
        missing.append(str(INGEST_RESULTS))
    if not API_RESULTS.exists():
        missing.append(str(API_RESULTS))

    if missing:
        for path in missing:
            print(f"ERROR: Result file not found: {path}", file=sys.stderr)
        print(
            "Run load tests first: make load-test"
            " (or run ingest_load.py and api_load.py individually)",
            file=sys.stderr,
        )
        sys.exit(1)

    ingest_data = json.loads(INGEST_RESULTS.read_text())
    api_data = json.loads(API_RESULTS.read_text())
    return ingest_data, api_data


def generate_markdown(ingest_data: dict, api_data: dict) -> str:
    """Generate the load-report.md content."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append("# CutPoint Load Test Report")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append("")

    # Insert benchmark table
    insert_benchmarks = ingest_data.get("insert_benchmarks", [])
    if insert_benchmarks:
        lines.append("## Insert Benchmarks (ClickHouse)")
        lines.append("")
        lines.append("| Batch Size | p50 (ms) | p95 (ms) | p99 (ms) | Rows/sec |")
        lines.append("|-----------|----------|----------|----------|----------|")
        for row in insert_benchmarks:
            lines.append(
                f"| {row['batch_size']:,} | {row['p50_ms']:.2f} | "
                f"{row['p95_ms']:.2f} | {row['p99_ms']:.2f} | "
                f"{row['rows_per_sec']:,.0f} |"
            )
        lines.append("")

    # Query benchmark table
    query_benchmarks = ingest_data.get("query_benchmarks", {})
    if query_benchmarks:
        lines.append("## Query Benchmarks")
        lines.append("")
        lines.append("| Query | p50 (ms) | p95 (ms) | p99 (ms) |")
        lines.append("|-------|----------|----------|----------|")
        for query_name, metrics in query_benchmarks.items():
            lines.append(
                f"| {query_name} | {metrics['p50_ms']:.2f} | "
                f"{metrics['p95_ms']:.2f} | {metrics['p99_ms']:.2f} |"
            )
        lines.append("")

    # API load test table
    lines.append("## API Load Test")
    lines.append("")
    lines.append("| Endpoint | Concurrency | p50 (ms) | p95 (ms) | p99 (ms) | Error Rate |")
    lines.append("|----------|------------|----------|----------|----------|-----------|")

    for endpoint_key in ("trailers_endpoint", "report_endpoint", "analyze_endpoint"):
        endpoint_data = api_data.get(endpoint_key, [])
        display_name = endpoint_key.replace("_endpoint", "").replace("_", " ").title()
        for row in endpoint_data:
            error_pct = row.get("error_rate", 0) * 100
            lines.append(
                f"| GET /{display_name.lower().replace(' ', '-')} | {row['concurrency']} | "
                f"{row['p50_ms']:.2f} | {row['p95_ms']:.2f} | {row['p99_ms']:.2f} | "
                f"{error_pct:.1f}% |"
            )
    lines.append("")

    # Threshold check
    threshold = api_data.get("threshold_check", {})
    if threshold:
        status = "PASS" if threshold.get("passed") else "FAIL"
        lines.append("## Threshold Check")
        lines.append("")
        lines.append(f"- **Target**: {threshold.get('target', 'N/A')}")
        lines.append(f"- **Actual p99**: {threshold.get('actual_p99_ms', 'N/A')} ms")
        lines.append(f"- **Result**: {status}")
        lines.append("")

    # Infrastructure note
    lines.append("## Infrastructure Note")
    lines.append("")
    lines.append(
        "All benchmarks were run against a **local standalone ClickHouse binary** "
        "(`.local-clickhouse/clickhouse server`), not ClickHouse Cloud. "
        "Production latencies on ClickHouse Cloud may differ due to network overhead, "
        "shared resources, and different hardware profiles. "
        "Thresholds are set conservatively for local dev: p99 < 2000ms at 50 concurrent requests."
    )
    lines.append("")
    lines.append("![Latency vs Concurrency](load-chart.png)")
    lines.append("")

    return "\n".join(lines)


def generate_chart(api_data: dict) -> None:
    """Generate the latency-vs-concurrency line chart as docs/perf/load-chart.png."""
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10, 6))

    endpoints = {
        "trailers_endpoint": "GET /trailers",
        "report_endpoint": "GET /report",
    }
    percentiles = ("p50_ms", "p95_ms", "p99_ms")
    percentile_labels = ("p50", "p95", "p99")
    linestyles = ("-", "--", ":")
    colors = {
        "trailers_endpoint": ("#2563eb", "#60a5fa", "#93c5fd"),  # blues
        "report_endpoint": ("#dc2626", "#f87171", "#fca5a5"),  # reds
    }

    for endpoint_key, endpoint_label in endpoints.items():
        data = api_data.get(endpoint_key, [])
        if not data:
            continue

        concurrency_levels = [row["concurrency"] for row in data]
        color_set = colors[endpoint_key]

        for i, (pct, pct_label) in enumerate(zip(percentiles, percentile_labels)):
            values = [row[pct] for row in data]
            ax.plot(
                concurrency_levels,
                values,
                marker="o",
                linestyle=linestyles[i],
                color=color_set[i],
                linewidth=2,
                markersize=6,
                label=f"{endpoint_label} {pct_label}",
            )

    ax.set_xlabel("Concurrency Level", fontsize=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("CutPoint API Latency vs Concurrency", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", fontsize=10)
    ax.set_xticks([10, 50, 100])

    plt.tight_layout()
    chart_path = PERF_DIR / "load-chart.png"
    fig.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Chart saved: {chart_path}")


def main() -> None:
    """Main entry point."""
    # Ensure output directory exists
    PERF_DIR.mkdir(parents=True, exist_ok=True)

    ingest_data, api_data = load_results()

    # Generate markdown report
    report_md = generate_markdown(ingest_data, api_data)
    report_path = PERF_DIR / "load-report.md"
    report_path.write_text(report_md)
    print(f"Report saved: {report_path}")

    # Generate chart
    generate_chart(api_data)

    # Summary
    threshold = api_data.get("threshold_check", {})
    if threshold:
        status = "PASS" if threshold.get("passed") else "FAIL"
        print(f"Threshold check: {status} (p99={threshold.get('actual_p99_ms')}ms)")


if __name__ == "__main__":
    main()
