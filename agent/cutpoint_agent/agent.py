"""Root SequentialAgent -- the deterministic 5-step CutPoint diagnostic pipeline.

Step order is fixed by code (SequentialAgent), never chosen by an LLM:
  [1] analyst        -- retention curve, changepoints and funnel read directly
                        from ClickHouse over a readonly=1 connection
  [2] extractor      -- deterministic clip extraction around each cliff
  [3] diagnostician  -- Gemini multimodal perception per clip (via Vertex AI)
  [4] narrator       -- Gemini writes the editor-facing summary from the verified
                        numbers, with mcp-clickhouse available for context
  [5] reporter       -- merges everything into Director's Notes

The model appears in steps 3 and 4 only, and in neither case can it put a number
in the report. Step 1 used to be an LlmAgent transcribing query results; on a real
run it reported a cliff that does not exist and missed all three that do, so the
numbers were moved off it entirely. See steps/analyst.py.

ADK entrypoint: `root_agent`, so `adk web` / `adk run` work against this module.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import SequentialAgent

from agent.cutpoint_agent.steps.analyst import build_analyst_agent
from agent.cutpoint_agent.steps.diagnostician import DiagnosticianAgent
from agent.cutpoint_agent.steps.extractor import ExtractorAgent
from agent.cutpoint_agent.steps.narrator import build_narrator_agent
from agent.cutpoint_agent.steps.reporter import ReporterAgent

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def build_root_agent() -> SequentialAgent:
    return SequentialAgent(
        name="cutpoint_agent",
        description=(
            "Deterministic 5-step pipeline: ClickHouse retention analysis, clip "
            "extraction, Gemini video diagnosis, Gemini narrative summary, "
            "Director's Notes report. No model produces a number."
        ),
        sub_agents=[
            build_analyst_agent(),
            ExtractorAgent(name="extractor"),
            DiagnosticianAgent(name="diagnostician"),
            build_narrator_agent(),
            ReporterAgent(name="reporter"),
        ],
    )


root_agent = build_root_agent()
