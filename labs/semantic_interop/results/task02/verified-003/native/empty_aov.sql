-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  CAST(MAX(subq_6.revenue) AS DOUBLE) / CAST(NULLIF(MAX(subq_13.order_count), 0) AS DOUBLE) AS average_order_value
FROM (
  -- Constrain Output with WHERE
  -- Select: ['__revenue']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(revenue) AS revenue
  FROM (
    -- Read Elements From Semantic Model 'items'
    -- Metric Time Dimension 'order_date'
    -- Select: ['__revenue', 'metric_time__day']
    SELECT
      DATE_TRUNC('day', order_date) AS metric_time__day
      , amount AS revenue
    FROM "benchmark"."main"."items" items_src_10000
  ) subq_2
  WHERE metric_time__day < '2020-01-01'
) subq_6
CROSS JOIN (
  -- Constrain Output with WHERE
  -- Select: ['__order_count']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    COUNT(DISTINCT order_count) AS order_count
  FROM (
    -- Read Elements From Semantic Model 'orders'
    -- Metric Time Dimension 'order_date'
    -- Select: ['__order_count', 'metric_time__day']
    SELECT
      DATE_TRUNC('day', order_date) AS metric_time__day
      , order_id AS order_count
    FROM "benchmark"."main"."orders" orders_src_10000
  ) subq_9
  WHERE metric_time__day < '2020-01-01'
) subq_13
