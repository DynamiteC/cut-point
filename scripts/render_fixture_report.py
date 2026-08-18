"""Renders tests/fixtures/directors_notes_fixture.json into data/reports/fixture.html
and fixture.md, so the Phase 6 gate artifact TASK.md asks for actually exists on
disk for manual inspection ("data/reports/fixture.html opens with curve + cliff
cards").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.cutpoint_agent.schemas import DirectorsNotes
from report.render import render_html, render_markdown

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "directors_notes_fixture.json"
OUT_DIR = REPO_ROOT / "data" / "reports"


def main() -> int:
    notes = DirectorsNotes.model_validate(json.loads(FIXTURE_PATH.read_text()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "fixture.html").write_text(render_html(notes))
    (OUT_DIR / "fixture.md").write_text(render_markdown(notes))
    print(f"wrote {OUT_DIR / 'fixture.html'}")
    print(f"wrote {OUT_DIR / 'fixture.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
