CREATE TABLE IF NOT EXISTS cutpoint.mv_second_viewers
(
    event_date      Date,
    trailer_id      LowCardinality(String),
    cohort          LowCardinality(String),
    second_offset   UInt16,
    viewers_state   AggregateFunction(uniq, UUID)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (trailer_id, cohort, second_offset);

CREATE MATERIALIZED VIEW IF NOT EXISTS cutpoint.mv_second_viewers_mv
TO cutpoint.mv_second_viewers
AS SELECT
    event_date, trailer_id, cohort, second_offset,
    uniqState(session_id) AS viewers_state
FROM cutpoint.raw_playback_events
WHERE event_type = 'heartbeat'
GROUP BY event_date, trailer_id, cohort, second_offset;
