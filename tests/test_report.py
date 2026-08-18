"""Phase 6 gate: golden-file test from a fixture DirectorsNotes JSON. Asserts the
rendered HTML contains key DOM strings (curve svg, cliff cards, milestones).
"""

import json
from pathlib import Path

from agent.cutpoint_agent.schemas import DirectorsNotes
from report.render import render_html, render_markdown

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "directors_notes_fixture.json"


def load_fixture_notes() -> DirectorsNotes:
    return DirectorsNotes.model_validate(json.loads(FIXTURE_PATH.read_text()))


def test_render_html_contains_key_dom_strings():
    notes = load_fixture_notes()
    html = render_html(notes)

    assert "<svg" in html
    assert "polyline" in html
    assert "Director&#39;s Notes: Demo One" in html or "Director's Notes: Demo One" in html
    assert "Second 47" in html
    assert "18-24" in html
    assert "25-34" in html
    assert "replace_shot" in html
    assert "reached_25pct" in html


def test_render_html_is_self_contained_no_cdn():
    notes = load_fixture_notes()
    html = render_html(notes)
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn." not in html.lower()


def test_render_markdown_contains_all_sections():
    notes = load_fixture_notes()
    md = render_markdown(notes)
    assert "# Director's Notes: Demo One" in md
    assert "## Executive Summary" in md
    assert "## Milestone Funnel" in md
    assert "## Retention Cliffs" in md
    assert "Second 47" in md


def test_golden_file_report_writes_both_formats(tmp_path):
    from report.render import render_report_from_json

    out_path = tmp_path / "fixture.json"
    out_path.write_text(FIXTURE_PATH.read_text())

    md_path, html_path = render_report_from_json(out_path, out_dir=tmp_path)

    assert md_path.exists()
    assert html_path.exists()
    assert "<svg" in html_path.read_text()
