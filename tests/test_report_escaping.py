"""The HTML report embeds free-form Gemini output. Autoescaping was silently off
because select_autoescape(["html"]) matches the final extension and the template
is named report.html.jinja. api/main.py serves that file back as text/html, so an
unescaped model string is stored XSS on the API origin.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.cutpoint_agent.schemas import DirectorsNotes
from report.render import render_html, render_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "directors_notes_fixture.json"
PAYLOAD = '<img src=x onerror=alert(document.domain)>'


def _notes_with_payload() -> DirectorsNotes:
    raw = json.loads(FIXTURE.read_text())
    raw["title"] = PAYLOAD
    raw["executive_summary"] = PAYLOAD
    raw["cliffs"][0]["on_screen"] = PAYLOAD
    raw["cliffs"][0]["hypothesis"] = PAYLOAD
    raw["cliffs"][0]["recommendations"][0]["rationale"] = PAYLOAD
    return DirectorsNotes.model_validate(raw)


def test_html_report_escapes_model_authored_strings() -> None:
    # Arrange
    notes = _notes_with_payload()

    # Act
    html = render_html(notes)

    # Assert
    assert PAYLOAD not in html, "raw payload reached the HTML report unescaped"
    assert "&lt;img src=x onerror=alert(document.domain)&gt;" in html


def test_markdown_report_is_not_html_escaped() -> None:
    # Arrange
    notes = _notes_with_payload()

    # Act
    md = render_markdown(notes)

    # Assert: escaping Markdown would corrupt it, so it must stay verbatim
    assert "&lt;" not in md
