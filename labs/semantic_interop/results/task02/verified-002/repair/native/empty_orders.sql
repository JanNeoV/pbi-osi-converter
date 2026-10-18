-- Constrain Output with WHERE
-- Select: ['__order_count']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
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
) subq_2
WHERE metric_time__day < '2020-01-01'
