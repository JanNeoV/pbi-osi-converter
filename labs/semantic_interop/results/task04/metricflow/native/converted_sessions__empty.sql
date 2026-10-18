-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10002_cte AS (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  SELECT
    DATE_TRUNC('day', session_date) AS metric_time__day
    , session_id AS session
    , is_eligible
    , is_eligible AS session__is_eligible
    , session_id AS __eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
)

SELECT
  COALESCE(converted_sessions_base, 0) + 0 * eligible_sessions AS converted_sessions
FROM (
  -- Combine Aggregated Outputs
  SELECT
    MAX(subq_10.converted_sessions_base) AS converted_sessions_base
    , MAX(subq_16.eligible_sessions) AS eligible_sessions
  FROM (
    -- Constrain Output with WHERE
    -- Select: ['__converted_sessions_base']
    -- Aggregate Inputs for Simple Metrics
    -- Compute Metrics via Expressions
    SELECT
      COUNT(DISTINCT converted_sessions_base) AS converted_sessions_base
    FROM (
      -- Join Standard Outputs
      -- Select: ['__converted_sessions_base', 'session__is_eligible', 'order__order_status', 'metric_time__day']
      SELECT
        DATE_TRUNC('day', orders_src_10000.session_date) AS metric_time__day
        , orders_src_10000.order_status AS order__order_status
        , sma_10002_cte.is_eligible AS session__is_eligible
        , orders_src_10000.session_id AS converted_sessions_base
      FROM "benchmark"."main"."orders" orders_src_10000
      LEFT OUTER JOIN
        sma_10002_cte
      ON
        orders_src_10000.session_id = sma_10002_cte.session
    ) subq_6
    WHERE (
      session__is_eligible = true AND order__order_status = 'paid'
    ) AND (
      metric_time__day < '2020-01-01'
    )
  ) subq_10
  CROSS JOIN (
    -- Constrain Output with WHERE
    -- Select: ['__eligible_sessions']
    -- Aggregate Inputs for Simple Metrics
    -- Compute Metrics via Expressions
    SELECT
      COUNT(DISTINCT eligible_sessions) AS eligible_sessions
    FROM (
      -- Read From CTE For node_id=sma_10002
      -- Select: ['__eligible_sessions', 'session__is_eligible', 'metric_time__day']
      SELECT
        metric_time__day
        , session__is_eligible
        , __eligible_sessions AS eligible_sessions
      FROM sma_10002_cte
    ) subq_12
    WHERE (session__is_eligible = true) AND (metric_time__day < '2020-01-01')
  ) subq_16
) subq_17
