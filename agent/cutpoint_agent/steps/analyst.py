"""Step 1: analyst -- executes the fixed SQL analysis templates via mcp-clickhouse
and returns a structured AnalysisResult. See TASK.md section 7.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.genai import types

from agent.cutpoint_agent.config import gemini_model
from agent.cutpoint_agent.mcp import clickhouse_toolset
from agent.cutpoint_agent.prompts import ANALYST_INSTRUCTION
from agent.cutpoint_agent.schemas import AnalysisResult


def build_analyst_agent() -> LlmAgent:
    return LlmAgent(
        name="analyst",
        model=gemini_model(),
        instruction=ANALYST_INSTRUCTION,
        # Query 1 returns thousands of per-second rows. Left unbounded the model
        # started rendering them as a table inside a JSON string and ran out of
        # output tokens mid-string, so the whole step died on invalid JSON.
        # The cap makes an over-long answer fail fast instead of at 53KB.
        generate_content_config=types.GenerateContentConfig(max_output_tokens=8192),
        tools=[clickhouse_toolset()],
        output_schema=AnalysisResult,
        output_key="analysis_result",
    )
