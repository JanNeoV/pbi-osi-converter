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
  -- Select: ['__revenue', 'item_id__status']
  SELECT
    status AS item_id__status
    , amount AS revenue
  FROM "benchmark"."main"."regression_items" items_src_10000
) subq_2
WHERE item_id__status = 'completed'
