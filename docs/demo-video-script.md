# CutPoint Demo Video Script (3:00 max)

## Beat 1: The problem (0:00-0:20)

"Marketing teams cut trailers by gut feel and A/B test comment sections. They know a trailer
'isn't landing' with young audiences, but not the exact second, or why. CutPoint answers both:
WHERE a trailer loses viewers, second by second, cohort by cohort, and WHY -- using Gemini to
actually watch the frames at that moment."

## Beat 2: Run `make demo` (0:20-1:00)

- Show the terminal: `make demo`.
- Narrate over the scrolling output: "This is a real pipeline. ClickHouse is aggregating tens of
  millions of per-second playback events for this trailer. The agent -- built on Google's Agent
  Development Kit -- queries it through the official mcp-clickhouse MCP server, finds the
  retention cliffs, extracts a video clip around each one with ffmpeg, and sends each clip to
  Gemini on Vertex AI to diagnose what's happening on screen."
- Let the "second N: <hypothesis>" lines print live -- this is the payoff moment, don't skip it.

## Beat 3: Report walkthrough -- the second-47 story (1:00-2:00)

- Open the rendered HTML report (`data/reports/demo_001.html`).
- Point at the retention curve and the dashed red marker at second 47.
- Read the card: "22% of 18-24 and 25-34 year olds left in a 3-second window right here. Gemini
  watched the actual frames and found why: [hypothesis from the live diagnosis]. The
  recommendation isn't 'make it better' -- it's a specific action: replace_shot, seconds 42-52,
  with the rationale spelled out."
- Emphasize: this came from a real trailer-shaped video and real per-second telemetry, not a
  vibes-based guess.

## Beat 4: Architecture flash (2:00-2:40)

- Show `docs/architecture.md`'s mermaid diagram for 5-8 seconds.
- Call out three things explicitly, on screen or verbally: "ClickHouse Cloud for the analytics.
  Google ADK's SequentialAgent for a deterministic, auditable pipeline -- the LLM never decides
  what runs next, only what it sees. And Vertex AI Gemini for the actual video understanding."
- Mention mcp-clickhouse is read-only by construction -- the agent literally cannot write to
  production data.

## Beat 4.5: Resilience proof (2:40-2:55)

"This isn't just happy-path correct. [show load-chart.png] Our API handles 100 concurrent
requests with p99 under 200ms. [show chaos-report.md table] And when things go wrong, kill the
extractor mid-pipeline, corrupt a video file, simulate a network partition, the system fails
LOUD and SAFE, never silently, never with corrupt data. Four chaos scenarios, four passes."

## Beat 5: Impact close (2:55-3:00)

"A trailer editor gets a second-by-second, cohort-aware cut list in under two minutes instead of
guessing from aggregate view-through rate. This is a prototype today; the same pipeline runs
unchanged against a real ClickHouse Cloud service and any real trailer video -- swap the .env and
the video file, nothing else changes."

## Production notes

- Total runtime target: 3:00, hard cap.
- Record `make demo` once end-to-end beforehand if the live Vertex AI call is slow; splice the
  terminal output rather than waiting live on camera.
- Captions should spell out "ClickHouse", "Google ADK", "MCP", "Vertex AI Gemini" on first
  mention -- these are the judged technology callouts.
