# CutPoint Frontend Build Prompt (for Replit Agent)

Paste this entire file as the first message to Replit Agent, with a new Repl.

## Product

CutPoint is a tool for studio marketing teams: it tells you exactly WHERE a trailer loses
viewers (per-second audience retention analytics) and WHY (Gemini's frame-level video
understanding of the exact moment), then gives timestamped recut recommendations ("Director's
Notes").

Build a web frontend for CutPoint that talks to an existing backend API (already deployed,
see contract below). Do not build any backend logic -- this is a pure frontend project that
calls the API contract below.

Deploy target: replit.app (this must run and be reachable at a *.replit.app URL).

## Brand direction

Dark studio aesthetic: think a film editing suite at night, not a SaaS dashboard.

- Background: near-black (#0f1115), secondary surfaces slightly lighter (#1a1d24)
- Accent: warm amber (#e8a63d) -- used for the retention curve, primary buttons, and highlights
- Danger/cliff markers: soft red (#ff5c5c), dashed lines
- Typography: a monospace or condensed sans for numbers/timestamps (evokes timecode displays),
  a clean sans for body copy. Do not use a default Bootstrap/Material look.
- Motion: subtle -- a timeline scrubber that highlights cliffs on hover, cards that lift slightly
  on hover. No bouncy animations.

## API contract (base URL: provided via environment variable API_BASE_URL)

### GET /trailers
Returns a list of trailer ids, e.g. `["demo_001", "demo_002", "demo_003"]`.

### POST /analyze
Request body: `{"trailer_id": "demo_001"}`
Response: `{"report_id": "demo_001"}`
This call runs synchronously in the prototype and can take up to a minute (real ClickHouse
query + Gemini video calls) -- show a progress state while waiting, do not assume it returns
instantly.

### GET /report/{trailer_id}
Returns the full DirectorsNotes JSON:

```json
{
  "trailer_id": "demo_001",
  "title": "Demo One",
  "duration_s": 90,
  "analyzed_at": "2026-08-19T00:00:00Z",
  "overall_retention_end": 0.35,
  "milestone_funnel": {
    "reached_25pct": 0.70,
    "reached_50pct": 0.53,
    "reached_75pct": 0.42,
    "completed": 0.35
  },
  "cliffs": [
    {
      "second": 47,
      "drop_pct": 0.22,
      "affected_cohorts": ["18-24", "25-34"],
      "z_score": -17.4,
      "clip_path": "data/clips/demo_001_47.mp4",
      "on_screen": "a sudden tonal shift into a jump-scare reveal",
      "hypothesis": "the villain reveal spoils the twist for younger viewers",
      "severity": 4,
      "recommendations": [
        {
          "action": "replace_shot",
          "target_range_s": [42, 52],
          "rationale": "the villain reveal spoils the twist; recommend replace_shot around second 47."
        }
      ]
    }
  ],
  "executive_summary": "demo_001 loses the most viewers at second 47 (22.0% drop among 18-24, 25-34): the villain reveal spoils the twist for younger viewers. 1 cliff(s) total flagged for recut review."
}
```

### GET /report/{trailer_id}/html
Returns a pre-rendered, self-contained HTML report (server-side rendered retention curve +
cliff cards). Useful as a fallback/print view; you do not need to use this for the main UI --
build the primary experience from the JSON above.

CORS is open on the API (`Access-Control-Allow-Origin: *`), so call it directly from the
browser.

## Screens

### 1. Trailer picker (landing screen)
- List trailers from `GET /trailers` as cards (title-cased trailer_id, e.g. "Demo 001").
- Each card has an "Analyze" button.
- If a report already exists for a trailer (you won't know this ahead of time -- just try
  `GET /report/{trailer_id}` first; a 404 means "not yet analyzed"), show a "View Report" button
  instead of "Analyze".

### 2. Analyze progress screen
- After clicking "Analyze", POST to `/analyze` and show an indeterminate progress state (this
  is a real multi-cloud pipeline call and can take 30-90 seconds).
- Suggested copy while waiting: "Querying ClickHouse for retention cliffs...", "Extracting video
  segments...", "Asking Gemini what's happening on screen..." (cycle through these as simple
  timed text, since the API is synchronous and does not stream progress in this prototype).
- On success, navigate to the report view. On error, show the error message from the API
  response body and a retry button.

### 3. Report view
- Header: trailer title, duration, overall retention at end (big number, amber).
- Milestone funnel: four stat tiles (25% / 50% / 75% / completed) with percentages.
- Retention timeline: a horizontal timeline spanning 0 to duration_s, with:
  - The retention curve (you can approximate a smooth curve through the milestone points and
    cliff points, or just plot the cliff markers on a baseline -- the backend does not expose a
    full per-second curve to the frontend in this prototype).
  - A vertical dashed red marker at each cliff's `second`.
  - Clicking a marker scrolls to / highlights that cliff's card below.
- Cliff cards (one per entry in `cliffs`, ordered by `second`):
  - Second + drop_pct + z_score in the header (e.g. "Second 47 -- 22% drop, z=-17.4")
  - Cohort tags (pill badges) for `affected_cohorts`
  - "On screen" description
  - "Hypothesis" (the causal explanation)
  - Severity shown as a 1-5 filled-dot indicator
  - Recommendations list: action (bold), target range, rationale
  - A link/reference to `clip_path` (the prototype does not need to actually stream video --
    displaying the path or a placeholder thumbnail is fine)

## Non-goals for this frontend build

- Do not implement authentication.
- Do not implement video playback of the clips (path display / placeholder is sufficient).
- Do not build any of the backend, ClickHouse, or Gemini logic -- it already exists and is
  reached only through the API contract above.
- Do not add a build step that requires secrets beyond `API_BASE_URL`.

## Definition of done

- Deployed on replit.app and reachable at a public URL.
- Trailer picker, analyze flow, and report view all work end to end against the live API.
- Dark studio aesthetic with amber accent, not a default component-library look.
- Responsive down to a reasonable mobile width (this is a marketing-team internal tool, desktop
  is the primary target, but do not break on a laptop-width viewport).
