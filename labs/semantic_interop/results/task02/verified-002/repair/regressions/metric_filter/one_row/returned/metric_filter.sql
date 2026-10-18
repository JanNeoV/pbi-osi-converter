-- Read Elements From Semantic Model 'items'
-- Metric Time Dimension 'order_date'
-- Select: ['__revenue']
-- Select: ['__revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  SUM(SUM(CASE WHEN item_id__status = 'completed' THEN items.amount END)) AS revenue
FROM "benchmark"."main"."regression_items" items_src_10000
