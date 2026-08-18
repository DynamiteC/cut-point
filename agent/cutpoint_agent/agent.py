"""Root SequentialAgent -- the deterministic 5-step CutPoint diagnostic pipeline.

Step order is fixed by code (SequentialAgent), never chosen by an LLM:
  [1] analyst        -- ClickHouse retention curve + changepoints (via mcp-clickhouse)
  [2] extractor      -- deterministic clip extraction around each cliff
  [3] diagnostician  -- Gemini multimodal perception per clip (via Vertex AI)
  [4] reporter        -- merges everything into Director's Notes

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

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


def build_root_agent() -> SequentialAgent:
    return SequentialAgent(
        name="cutpoint_agent",
        description=(
            "Deterministic 4-step pipeline: ClickHouse retention analysis, clip "
            "extraction, Gemini video diagnosis, Director's Notes report."
        ),
        sub_agents=[
            build_analyst_agent(),
            ExtractorAgent(name="extractor"),
            DiagnosticianAgent(name="diagnostician"),
            ReporterAgent(name="reporter"),
        ],
    )


root_agent = build_root_agent()
