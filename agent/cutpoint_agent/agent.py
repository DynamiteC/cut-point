"""Root SequentialAgent -- the deterministic 5-step CutPoint diagnostic pipeline.

Step order is fixed by code (SequentialAgent), never chosen by an LLM:
  [1] analyst        -- ClickHouse retention curve + changepoints (via mcp-clickhouse)
  [2] validator      -- re-derives the same statistics over a readonly=1
                        connection and overrules the analyst's transcription
  [3] extractor      -- deterministic clip extraction around each cliff
  [4] diagnostician  -- Gemini multimodal perception per clip (via Vertex AI)
  [5] reporter       -- merges everything into Director's Notes

Only step 1 and step 4 involve a model at all, and step 2 exists so that step 1
being a model does not put the numbers at risk.

ADK entrypoint: `root_agent`, so `adk web` / `adk run` work against this module.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import SequentialAgent

from agent.cutpoint_agent.steps.analyst import build_analyst_agent
from agent.cutpoint_agent.steps.diagnostician import DiagnosticianAgent
from agent.cutpoint_agent.steps.extractor import ExtractorAgent
from agent.cutpoint_agent.steps.reporter import ReporterAgent
from agent.cutpoint_agent.steps.validator import ValidatorAgent

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def build_root_agent() -> SequentialAgent:
    return SequentialAgent(
        name="cutpoint_agent",
        description=(
            "Deterministic 5-step pipeline: ClickHouse retention analysis, "
            "validation of those numbers against the database, clip extraction, "
            "Gemini video diagnosis, Director's Notes report."
        ),
        sub_agents=[
            build_analyst_agent(),
            ValidatorAgent(name="validator"),
            ExtractorAgent(name="extractor"),
            DiagnosticianAgent(name="diagnostician"),
            ReporterAgent(name="reporter"),
        ],
    )


root_agent = build_root_agent()
