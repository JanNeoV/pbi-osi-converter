-- Constrain Output with WHERE
-- Select: ['__revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
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
WHERE metric_time__day >= '2025-12-31' AND metric_time__day < '2026-01-02'
