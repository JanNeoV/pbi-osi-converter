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

SELECT
  paid_revenue_base + 0 * eligible_sessions AS paid_revenue
FROM (
  -- Combine Aggregated Outputs
  SELECT
    MAX(subq_10.paid_revenue_base) AS paid_revenue_base
    , MAX(subq_16.eligible_sessions) AS eligible_sessions
  FROM (
    -- Constrain Output with WHERE
    -- Select: ['__paid_revenue_base']
    -- Aggregate Inputs for Simple Metrics
    -- Compute Metrics via Expressions
    SELECT
      SUM(paid_revenue_base) AS paid_revenue_base
    FROM (
      -- Join Standard Outputs
      -- Select: ['__paid_revenue_base', 'session__is_eligible', 'order__order_status']
      SELECT
        orders_src_10000.order_status AS order__order_status
        , sma_10002_cte.is_eligible AS session__is_eligible
        , orders_src_10000.amount_eur AS paid_revenue_base
      FROM "benchmark"."main"."orders" orders_src_10000
      LEFT OUTER JOIN
        sma_10002_cte
      ON
        orders_src_10000.session_id = sma_10002_cte.session
    ) subq_6
    WHERE session__is_eligible = true AND order__order_status = 'paid'
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
      -- Select: ['__eligible_sessions', 'session__is_eligible']
      SELECT
        session__is_eligible
        , __eligible_sessions AS eligible_sessions
      FROM sma_10002_cte
    ) subq_12
    WHERE session__is_eligible = true
  ) subq_16
) subq_17
