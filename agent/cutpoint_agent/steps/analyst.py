"""Step 1: analyst -- executes the fixed SQL analysis templates via mcp-clickhouse
and returns a structured AnalysisResult. See TASK.md section 7.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from agent.cutpoint_agent.mcp import clickhouse_toolset
from agent.cutpoint_agent.prompts import ANALYST_INSTRUCTION
from agent.cutpoint_agent.schemas import AnalysisResult


def build_analyst_agent() -> LlmAgent:
    return LlmAgent(
        name="analyst",
        model=os.environ.get("GEMINI_MODEL", "gemini-3-flash"),
        instruction=ANALYST_INSTRUCTION,
        tools=[clickhouse_toolset()],
        output_schema=AnalysisResult,
        output_key="analysis_result",
    )
