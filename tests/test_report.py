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


def test_a_failed_clip_diagnosis_does_not_destroy_the_report():
    """Step 3 contains a per-clip failure and returns what it did get. The
    reporter then subscripted diagnosis_by_second and raised KeyError on the
    first missing one, so one timed-out clip destroyed the whole report and
    every paid inference in it. test_agent.py covered run_diagnostics; nothing
    covered the step after it.
    """
    from agent.cutpoint_agent.schemas import (
        AnalysisResult,
        Diagnosis,
        ExtractionResult,
    )
    from agent.cutpoint_agent.steps.reporter import build_directors_notes

    analysis = AnalysisResult(
        trailer_id="demo_001",
        overall_retention_end=0.35,
        milestone_funnel={"completed": 0.35},
        cliffs=[
            {"second": 10, "drop_pct": 0.2, "affected_cohorts": ["18-24"], "z_score": -5.0},
            {"second": 20, "drop_pct": 0.3, "affected_cohorts": ["25-34"], "z_score": -6.0},
        ],
    )
    extraction = ExtractionResult(
        trailer_id="demo_001",
        clips=[
            {"second": 10, "clip_path": "/tmp/a.mp4", "start_s": 5, "end_s": 15},
            {"second": 20, "clip_path": "/tmp/b.mp4", "start_s": 15, "end_s": 25},
        ],
    )
    # Gemini failed on second 20; only second 10 came back.
    diagnoses = [
        Diagnosis(second=10, on_screen="a static shot", hypothesis="slow", severity=3,
                  confidence=0.8)
    ]

    notes = build_directors_notes("demo_001", "Demo", 90, analysis, extraction, diagnoses)

    assert [c.second for c in notes.cliffs] == [10], "the surviving cliff must be reported"

    # Skipping silently was worse than the KeyError it replaced: the report
    # named second 10 as the most damaging moment and claimed "1 cliff(s)
    # total", when the database says the worst is second 20 at 30%.
    assert notes.diagnosis_failures, "the undiagnosed cliff must be recorded"
    assert notes.diagnosis_failures[0]["second"] == 20
    assert "1 of 2" in notes.executive_summary, (
        "the summary must not present partial coverage as complete"
    )


def test_public_clip_reference_never_leaks_a_local_path():
    """GET /report/{id} is public. An absolute local path there published the
    developer's home directory and advertised that the report came from a laptop
    rather than the deployed pipeline.
    """
    from agent.cutpoint_agent.steps.reporter import public_clip_ref

    assert public_clip_ref("/Users/someone/work/cut-point/data/clips/x.mp4") == "x.mp4"
    assert public_clip_ref("gs://a-bucket/clips/x.mp4") == "gs://a-bucket/clips/x.mp4"


def test_the_rendered_report_discloses_undiagnosed_cliffs():
    """A reader of the Markdown or the HTML must be able to see that a detected
    cliff has no explanation. The data existed but no surface rendered it.
    """
    import json
    from pathlib import Path as _P

    from agent.cutpoint_agent.schemas import DirectorsNotes
    from report.render import render_html, render_markdown

    raw = json.loads((_P(__file__).parent / "fixtures" / "directors_notes_fixture.json").read_text())
    raw["diagnosis_failures"] = [{"second": 63, "drop_pct": 0.18, "reason": "no diagnosis"}]
    notes = DirectorsNotes.model_validate(raw)

    for rendered in (render_markdown(notes), render_html(notes)):
        assert "could not be diagnosed" in rendered
        assert "63" in rendered
