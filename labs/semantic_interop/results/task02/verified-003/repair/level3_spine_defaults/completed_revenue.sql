-- Read Elements From Semantic Model 'orders'
-- Metric Time Dimension 'order_date'
-- Select: ['__completed_revenue']
-- Select: ['__completed_revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  SUM(SUM(CASE WHEN item__status = 'completed' THEN items.amount END)) AS completed_revenue
FROM "benchmark"."main"."orders" orders_src_10000
