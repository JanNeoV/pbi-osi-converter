-- Read Elements From Semantic Model 'items'
-- Metric Time Dimension 'order_date'
-- Select: ['__revenue', 'metric_time__day']
-- Select: ['__revenue', 'metric_time__day']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  DATE_TRUNC('day', order_date) AS metric_time__day
  , SUM(amount) AS revenue
FROM "benchmark"."main"."items" items_src_10000
GROUP BY
  DATE_TRUNC('day', order_date)
