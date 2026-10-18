-- Constrain Output with WHERE
-- Select: ['__eligible_sessions']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  COUNT(DISTINCT eligible_sessions) AS eligible_sessions
FROM (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  -- Select: ['__eligible_sessions', 'session__is_eligible', 'metric_time__day']
  SELECT
    DATE_TRUNC('day', session_date) AS metric_time__day
    , is_eligible AS session__is_eligible
    , session_id AS eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
) subq_2
WHERE (session__is_eligible = true) AND (metric_time__day < '2020-01-01')
