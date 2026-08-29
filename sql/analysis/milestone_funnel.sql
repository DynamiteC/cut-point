-- Milestone funnel (25% / 50% / 75% / complete) using ClickHouse's native windowFunnel().
-- This is a judged showcase query -- it must use windowFunnel, not manual step counting.
-- Params: {trailer_id}
WITH trailer_duration AS (
    -- cutpoint.trailers is a ReplacingMergeTree. Before a merge runs, several
    -- versions of a row coexist, so "LIMIT 1" without FINAL returned whichever
    -- part was read first and the funnel silently changed between runs.
    -- argMax picks the newest version deterministically regardless of merge state.
    SELECT argMax(duration_s, created_at) AS duration_s
    FROM cutpoint.trailers
    WHERE trailer_id = '{trailer_id}'
),
per_session AS (
    SELECT
        session_id,
        windowFunnel(86400)(
            toDateTime(event_ts),
            second_offset >= (SELECT duration_s FROM trailer_duration) * 0.25,
            second_offset >= (SELECT duration_s FROM trailer_duration) * 0.50,
            second_offset >= (SELECT duration_s FROM trailer_duration) * 0.75,
            event_type = 'complete'
        ) AS funnel_level
    FROM cutpoint.raw_playback_events
    WHERE trailer_id = '{trailer_id}'
      AND event_type IN ('heartbeat', 'complete')
    GROUP BY session_id
),
totals AS (
    SELECT count() AS total_sessions FROM per_session
)
SELECT
    'reached_25pct' AS milestone, countIf(funnel_level >= 1) / any(total_sessions) AS fraction
FROM per_session, totals
UNION ALL
SELECT
    'reached_50pct' AS milestone, countIf(funnel_level >= 2) / any(total_sessions) AS fraction
FROM per_session, totals
UNION ALL
SELECT
    'reached_75pct' AS milestone, countIf(funnel_level >= 3) / any(total_sessions) AS fraction
FROM per_session, totals
UNION ALL
SELECT
    'completed' AS milestone, countIf(funnel_level >= 4) / any(total_sessions) AS fraction
FROM per_session, totals
