"""Step 4: narrator -- the model's language job.

The pipeline deliberately gives the model nothing numeric to do. Every figure in
the report is read from ClickHouse in step 1. What a model is genuinely better at
than a format string is turning verified findings into an editor-facing
paragraph, so that is the job it gets here.

This is also where mcp-clickhouse earns its place: the narrator can look up
cohort divergence for supporting colour while writing, over a read-only tool
boundary, without ever being the source of a number in the report.

No output_schema on purpose. Forcing structured output through this model is what
produced whitespace-padded, truncated JSON in the previous design. Plain prose
cannot truncate into invalid syntax, and a failure here falls back to the
deterministic summary rather than ending the run.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from agent.cutpoint_agent import obs
from agent.cutpoint_agent.config import gemini_model
from agent.cutpoint_agent.mcp import clickhouse_toolset

NARRATOR_INSTRUCTION = """You are the narrator step of a fixed diagnostic pipeline for
video-trailer retention analysis. You are writing for a studio editor who will act on this.

Everything numeric in session state has already been computed by ClickHouse and is correct.
Do not recompute, re-derive, second-guess or restate figures beyond quoting them. Never invent
a second, a percentage or a cohort that is not present in the state you were given.

These are the ONLY facts you have. Anything not stated here does not exist.

VERIFIED ANALYTICS (computed by ClickHouse):
{analysis_result}

FRAME DIAGNOSES (what Gemini actually saw at each cliff):
{diagnoses}

Write ONE paragraph, 60 to 90 words, that an editor can act on:
- lead with the single most damaging moment and what happens on screen there
- name the cohorts that actually left
- end with the one change most likely to help

Plain prose. No markdown, no headings, no bullet points, no preamble. If the cliff list is
empty, say plainly that no statistically significant drop-off was detected.

Hard constraints, because a fabricated summary is worse than a plain one:
- Describe only what the frame diagnoses above actually say is on screen. Do not invent shots,
  effects, dialogue or tone that they do not mention.
- Quote only seconds that appear in the cliff list above.
- Do not state viewer counts. You have drop percentages, not counts.

You may call run_query for supporting context such as cohort divergence, but every fact in your
paragraph must trace to the data above."""


def build_narrator_llm() -> LlmAgent:
    return LlmAgent(
        name="narrator_llm",
        model=gemini_model(),
        instruction=NARRATOR_INSTRUCTION,
        tools=[clickhouse_toolset()],
        output_key="executive_summary",
        generate_content_config=types.GenerateContentConfig(
            max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_budget=512),
        ),
    )


def summary_is_grounded(summary: str, cliff_seconds: set[int]) -> tuple[bool, str]:
    """Reject a summary that cites a second which is not a detected cliff.

    A cheap, specific check rather than a general factuality claim. It exists
    because the first version of this step, given an instruction that only
    NAMED the state keys instead of interpolating them, received no data and
    confidently invented a CGI explosion and a viewer count. Grounding the
    prompt fixed the cause; this catches the next instance.
    """
    cited = {int(m) for m in re.findall(r"second (\d{1,4})", summary, flags=re.IGNORECASE)}
    invented = cited - cliff_seconds
    if invented:
        return False, f"cites seconds not detected as cliffs: {sorted(invented)}"
    return True, ""


class NarratorAgent(BaseAgent):
    """Runs the narrator, absorbs its failure, and rejects ungrounded prose.

    The report must exist even if the model does not cooperate, and it must not
    contain invented findings. Either way the reporter falls back to
    build_executive_summary(), the deterministic template.
    """

    narrator: LlmAgent

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        try:
            async for event in self.narrator.run_async(ctx):
                yield event

            summary = (ctx.session.state.get("executive_summary") or "").strip()
            if summary:
                cliffs = (ctx.session.state.get("analysis_result") or {}).get("cliffs", [])
                ok, why = summary_is_grounded(summary, {int(c["second"]) for c in cliffs})
                if not ok:
                    obs.warning("narrator summary rejected as ungrounded", reason=why)
                    yield Event(
                        author=self.name,
                        actions=EventActions(
                            state_delta={"executive_summary": "", "narrator_error": why}
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            detail = f"{type(exc).__name__}: {exc}"[:300]
            obs.warning(
                "narrator degraded, falling back to the deterministic summary",
                step="narrator",
                error=detail,
            )
            yield Event(
                author=self.name,
                actions=EventActions(state_delta={"narrator_error": detail}),
            )


def build_narrator_agent() -> NarratorAgent:
    return NarratorAgent(name="narrator", narrator=build_narrator_llm())
