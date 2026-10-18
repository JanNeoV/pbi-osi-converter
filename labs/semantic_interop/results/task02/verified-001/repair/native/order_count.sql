-- Read Elements From Semantic Model 'orders'
-- Metric Time Dimension 'order_date'
-- Select: ['__order_count']
-- Select: ['__order_count']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  COUNT(DISTINCT order_id) AS order_count
FROM "benchmark"."main"."orders" orders_src_10000
