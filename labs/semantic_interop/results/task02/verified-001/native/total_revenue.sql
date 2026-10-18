-- Read Elements From Semantic Model 'items'
-- Metric Time Dimension 'order_date'
-- Select: ['__revenue']
-- Select: ['__revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  SUM(amount) AS revenue
FROM "benchmark"."main"."items" items_src_10000
