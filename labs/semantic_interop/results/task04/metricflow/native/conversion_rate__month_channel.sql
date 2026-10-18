-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10002_cte AS (
  -- Read Elements From Semantic Model 'sessions'
  -- Metric Time Dimension 'session_date'
  SELECT
    DATE_TRUNC('month', session_date) AS metric_time__month
    , session_id AS session
    , sales_channel
    , is_eligible
    , sales_channel AS session__sales_channel
    , is_eligible AS session__is_eligible
    , session_id AS __eligible_sessions
  FROM "benchmark"."main"."sessions" sessions_src_10000
)

, cm_5_cte AS (
  -- Constrain Output with WHERE
  -- Select: ['__eligible_sessions', 'session__sales_channel', 'metric_time__month']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    metric_time__month
    , session__sales_channel
    , COUNT(DISTINCT eligible_sessions) AS eligible_sessions
  FROM (
    -- Read From CTE For node_id=sma_10002
    -- Select: ['__eligible_sessions', 'session__sales_channel', 'session__is_eligible', 'metric_time__month']
    SELECT
      metric_time__month
      , session__sales_channel
      , session__is_eligible
      , __eligible_sessions AS eligible_sessions
    FROM sma_10002_cte
  ) subq_12
  WHERE session__is_eligible = true
  GROUP BY
    metric_time__month
    , session__sales_channel
)

SELECT
  metric_time__month AS metric_time__month
  , session__sales_channel AS session__sales_channel
  , CAST(converted_sessions AS DOUBLE) / CAST(NULLIF(eligible_sessions, 0) AS DOUBLE) AS conversion_rate
FROM (
  -- Combine Aggregated Outputs
  SELECT
    COALESCE(subq_18.metric_time__month, cm_5_cte.metric_time__month) AS metric_time__month
    , COALESCE(subq_18.session__sales_channel, cm_5_cte.session__sales_channel) AS session__sales_channel
    , MAX(subq_18.converted_sessions) AS converted_sessions
    , MAX(cm_5_cte.eligible_sessions) AS eligible_sessions
  FROM (
    -- Compute Metrics via Expressions
    SELECT
      metric_time__month
      , session__sales_channel
      , COALESCE(converted_sessions_base, 0) + 0 * eligible_sessions AS converted_sessions
    FROM (
      -- Combine Aggregated Outputs
      SELECT
        COALESCE(subq_10.metric_time__month, cm_5_cte.metric_time__month) AS metric_time__month
        , COALESCE(subq_10.session__sales_channel, cm_5_cte.session__sales_channel) AS session__sales_channel
        , MAX(subq_10.converted_sessions_base) AS converted_sessions_base
        , MAX(cm_5_cte.eligible_sessions) AS eligible_sessions
      FROM (
        -- Constrain Output with WHERE
        -- Select: ['__converted_sessions_base', 'session__sales_channel', 'metric_time__month']
        -- Aggregate Inputs for Simple Metrics
        -- Compute Metrics via Expressions
        SELECT
          metric_time__month
          , session__sales_channel
          , COUNT(DISTINCT converted_sessions_base) AS converted_sessions_base
        FROM (
          -- Join Standard Outputs
          -- Select: [
          --   '__converted_sessions_base',
          --   'session__sales_channel',
          --   'session__is_eligible',
          --   'order__order_status',
          --   'metric_time__month',
          -- ]
          SELECT
            DATE_TRUNC('month', orders_src_10000.session_date) AS metric_time__month
            , orders_src_10000.order_status AS order__order_status
            , sma_10002_cte.sales_channel AS session__sales_channel
            , sma_10002_cte.is_eligible AS session__is_eligible
            , orders_src_10000.session_id AS converted_sessions_base
          FROM "benchmark"."main"."orders" orders_src_10000
          LEFT OUTER JOIN
            sma_10002_cte
          ON
            orders_src_10000.session_id = sma_10002_cte.session
        ) subq_6
        WHERE session__is_eligible = true AND order__order_status = 'paid'
        GROUP BY
          metric_time__month
          , session__sales_channel
      ) subq_10
      FULL OUTER JOIN
        cm_5_cte
      ON
        (
          subq_10.session__sales_channel = cm_5_cte.session__sales_channel
        ) AND (
          subq_10.metric_time__month = cm_5_cte.metric_time__month
        )
      GROUP BY
        COALESCE(subq_10.metric_time__month, cm_5_cte.metric_time__month)
        , COALESCE(subq_10.session__sales_channel, cm_5_cte.session__sales_channel)
    ) subq_17
  ) subq_18
  FULL OUTER JOIN
    cm_5_cte
  ON
    (
      subq_18.session__sales_channel = cm_5_cte.session__sales_channel
    ) AND (
      subq_18.metric_time__month = cm_5_cte.metric_time__month
    )
  GROUP BY
    COALESCE(subq_18.metric_time__month, cm_5_cte.metric_time__month)
    , COALESCE(subq_18.session__sales_channel, cm_5_cte.session__sales_channel)
) subq_20
