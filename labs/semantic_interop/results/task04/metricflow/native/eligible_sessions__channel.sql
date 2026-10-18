-- Constrain Output with WHERE
-- Select: ['__eligible_sessions', 'session__sales_channel']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  session__sales_channel
  , COUNT(DISTINCT eligible_sessions) AS eligible_sessions
FROM (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  -- Select: ['__eligible_sessions', 'session__sales_channel', 'session__is_eligible']
  SELECT
    sales_channel AS session__sales_channel
    , is_eligible AS session__is_eligible
    , session_id AS eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
) subq_2
WHERE session__is_eligible = true
GROUP BY
  session__sales_channel
