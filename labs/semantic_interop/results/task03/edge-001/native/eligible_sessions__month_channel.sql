-- Constrain Output with WHERE
-- Select: ['__eligible_sessions', 'session__sales_channel', 'metric_time__month']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  metric_time__month
  , session__sales_channel
  , COUNT(DISTINCT eligible_sessions) AS eligible_sessions
FROM (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  -- Select: ['__eligible_sessions', 'session__sales_channel', 'session__is_eligible', 'metric_time__month']
  SELECT
    DATE_TRUNC('month', session_date) AS metric_time__month
    , sales_channel AS session__sales_channel
    , is_eligible AS session__is_eligible
    , session_id AS eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
) subq_2
WHERE session__is_eligible = true
GROUP BY
  metric_time__month
  , session__sales_channel
