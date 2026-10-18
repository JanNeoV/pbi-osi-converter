-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10001_cte AS (
  -- Read Elements From Semantic Model 'orders'
  -- Metric Time Dimension 'session_date'
  SELECT
    DATE_TRUNC('day', session_date) AS metric_time__day
    , session_id AS session
    , order_status AS order__order_status
    , amount_eur AS __paid_revenue_base
    , order_id AS __paid_order_count_base
  FROM "benchmark"."main"."orders" orders_src_10000
)

, sma_10002_cte AS (
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

, cm_7_cte AS (
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
)

SELECT
  CAST(MAX(subq_18.paid_revenue) AS DOUBLE) / CAST(NULLIF(MAX(subq_30.paid_order_count), 0) AS DOUBLE) AS average_paid_order_value
FROM (
  -- Compute Metrics via Expressions
  SELECT
    paid_revenue_base + 0 * eligible_sessions AS paid_revenue
  FROM (
    -- Combine Aggregated Outputs
    SELECT
      MAX(subq_10.paid_revenue_base) AS paid_revenue_base
      , MAX(cm_7_cte.eligible_sessions) AS eligible_sessions
    FROM (
      -- Constrain Output with WHERE
      -- Select: ['__paid_revenue_base']
      -- Aggregate Inputs for Simple Metrics
      -- Compute Metrics via Expressions
      SELECT
        SUM(paid_revenue_base) AS paid_revenue_base
      FROM (
        -- Join Standard Outputs
        -- Select: ['__paid_revenue_base', 'session__is_eligible', 'order__order_status', 'metric_time__day']
        SELECT
          sma_10001_cte.metric_time__day AS metric_time__day
          , sma_10001_cte.order__order_status AS order__order_status
          , sma_10002_cte.is_eligible AS session__is_eligible
          , sma_10001_cte.__paid_revenue_base AS paid_revenue_base
        FROM sma_10001_cte
        LEFT OUTER JOIN
          sma_10002_cte
        ON
          sma_10001_cte.session = sma_10002_cte.session
      ) subq_6
      WHERE (
        session__is_eligible = true AND order__order_status = 'paid'
      ) AND (
        metric_time__day < '2020-01-01'
      )
    ) subq_10
    CROSS JOIN
      cm_7_cte
  ) subq_17
) subq_18
CROSS JOIN (
  -- Compute Metrics via Expressions
  SELECT
    COALESCE(paid_order_count_base, 0) + 0 * eligible_sessions AS paid_order_count
  FROM (
    -- Combine Aggregated Outputs
    SELECT
      MAX(subq_27.paid_order_count_base) AS paid_order_count_base
      , MAX(cm_7_cte.eligible_sessions) AS eligible_sessions
    FROM (
      -- Constrain Output with WHERE
      -- Select: ['__paid_order_count_base']
      -- Aggregate Inputs for Simple Metrics
      -- Compute Metrics via Expressions
      SELECT
        COUNT(DISTINCT paid_order_count_base) AS paid_order_count_base
      FROM (
        -- Join Standard Outputs
        -- Select: ['__paid_order_count_base', 'session__is_eligible', 'order__order_status', 'metric_time__day']
        SELECT
          sma_10001_cte.metric_time__day AS metric_time__day
          , sma_10001_cte.order__order_status AS order__order_status
          , sma_10002_cte.is_eligible AS session__is_eligible
          , sma_10001_cte.__paid_order_count_base AS paid_order_count_base
        FROM sma_10001_cte
        LEFT OUTER JOIN
          sma_10002_cte
        ON
          sma_10001_cte.session = sma_10002_cte.session
      ) subq_23
      WHERE (
        session__is_eligible = true AND order__order_status = 'paid'
      ) AND (
        metric_time__day < '2020-01-01'
      )
    ) subq_27
    CROSS JOIN
      cm_7_cte
  ) subq_29
) subq_30
