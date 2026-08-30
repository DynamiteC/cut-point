"""Single source of truth for the Gemini model id.

Both the diagnostician (direct google-genai call) and the narrator (ADK LlmAgent)
must agree; they previously carried divergent hardcoded fallbacks.
"""

from __future__ import annotations

import os

# All Things Agentic requires Gemini 3.5 or newer. Verified present on Vertex
# via `make preflight` (scripts/preflight.py::check_vertex_model).
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
