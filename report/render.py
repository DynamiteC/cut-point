"""Renders a DirectorsNotes JSON report into Markdown and a self-contained HTML
file (inline SVG retention curve, no CDN dependency).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from agent.cutpoint_agent.schemas import DirectorsNotes

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def build_retention_curve_points(notes: DirectorsNotes) -> list[tuple[float, float]]:
    """Approximate retention curve from milestone_funnel + cliffs (the report
    schema does not carry the full per-second curve -- see PROGRESS.md).
    """
    points = [(0.0, 1.0)]
    milestone_fractions = {
        "reached_25pct": 0.25,
        "reached_50pct": 0.50,
        "reached_75pct": 0.75,
        "completed": 1.0,
    }
    for key, x_fraction in milestone_fractions.items():
        if key in notes.milestone_funnel:
            points.append((x_fraction * notes.duration_s, notes.milestone_funnel[key]))
    for cliff in notes.cliffs:
        points.append((cliff.second, max(0.0, 1.0 - cliff.drop_pct)))
    points.sort(key=lambda p: p[0])
    return points


def build_svg_polyline(notes: DirectorsNotes, width: int = 760, height: int = 220) -> str:
    points = build_retention_curve_points(notes)
    if not points or notes.duration_s == 0:
        return ""
    coords = []
    for second, retention in points:
        x = (second / notes.duration_s) * width
        y = height - (retention * height)
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def build_cliff_marker_x(notes: DirectorsNotes, second: int, width: int = 760) -> float:
    if notes.duration_s == 0:
        return 0.0
    return (second / notes.duration_s) * width


def render_markdown(notes: DirectorsNotes) -> str:
    template = _env.get_template("report.md.jinja")
    return template.render(notes=notes)


def render_html(notes: DirectorsNotes) -> str:
    template = _env.get_template("report.html.jinja")
    return template.render(
        notes=notes,
        svg_polyline=build_svg_polyline(notes),
        cliff_marker_x=lambda second: build_cliff_marker_x(notes, second),
    )


def render_report_from_json(json_path: Path, out_dir: Path | None = None) -> tuple[Path, Path]:
    notes = DirectorsNotes.model_validate(json.loads(json_path.read_text()))
    out_dir = out_dir or json_path.parent

    md_path = out_dir / f"{notes.trailer_id}.md"
    html_path = out_dir / f"{notes.trailer_id}.html"
    md_path.write_text(render_markdown(notes))
    html_path.write_text(render_html(notes))
    return md_path, html_path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m report.render <path-to-directors-notes.json>")
        return 1
    md_path, html_path = render_report_from_json(Path(sys.argv[1]))
    print(f"wrote {md_path}")
    print(f"wrote {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
