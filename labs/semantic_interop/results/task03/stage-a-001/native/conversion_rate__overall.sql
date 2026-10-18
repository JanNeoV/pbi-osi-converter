-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10002_cte AS (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  SELECT
    session_id AS session
    , is_eligible
    , is_eligible AS session__is_eligible
    , session_id AS __eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
)

, cm_5_cte AS (
  -- Constrain Output with WHERE
  -- Select: ['__eligible_sessions']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    COUNT(DISTINCT eligible_sessions) AS eligible_sessions
  FROM (
    -- Read From CTE For node_id=sma_10002
    -- Select: ['__eligible_sessions', 'session__is_eligible']
    SELECT
      session__is_eligible
      , __eligible_sessions AS eligible_sessions
    FROM sma_10002_cte
  ) subq_12
  WHERE session__is_eligible = true
)

SELECT
  CAST(MAX(subq_18.converted_sessions) AS DOUBLE) / CAST(NULLIF(MAX(cm_5_cte.eligible_sessions), 0) AS DOUBLE) AS conversion_rate
FROM (
  -- Compute Metrics via Expressions
  SELECT
    COALESCE(converted_sessions_base, 0) + 0 * eligible_sessions AS converted_sessions
  FROM (
    -- Combine Aggregated Outputs
    SELECT
      MAX(subq_10.converted_sessions_base) AS converted_sessions_base
      , MAX(cm_5_cte.eligible_sessions) AS eligible_sessions
    FROM (
      -- Constrain Output with WHERE
      -- Select: ['__converted_sessions_base']
      -- Aggregate Inputs for Simple Metrics
      -- Compute Metrics via Expressions
      SELECT
        COUNT(DISTINCT converted_sessions_base) AS converted_sessions_base
      FROM (
        -- Join Standard Outputs
        -- Select: ['__converted_sessions_base', 'session__is_eligible', 'order__order_status']
        SELECT
          orders_src_10000.order_status AS order__order_status
          , sma_10002_cte.is_eligible AS session__is_eligible
          , orders_src_10000.session_id AS converted_sessions_base
        FROM "benchmark"."main"."orders" orders_src_10000
        LEFT OUTER JOIN
          sma_10002_cte
        ON
          orders_src_10000.session_id = sma_10002_cte.session
      ) subq_6
      WHERE session__is_eligible = true AND order__order_status = 'paid'
    ) subq_10
    CROSS JOIN
      cm_5_cte
  ) subq_17
) subq_18
CROSS JOIN
  cm_5_cte
