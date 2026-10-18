-- Constrain Output with WHERE
-- Select: ['__completed_revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  SUM(completed_revenue) AS completed_revenue
FROM (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__completed_revenue', 'item__status']
  SELECT
    status AS item__status
    , amount AS completed_revenue
  FROM "benchmark"."main"."items" items_src_10000
) subq_2
WHERE item__status = 'completed'
