-- Read Elements From Semantic Model 'orders'
-- Metric Time Dimension 'order_date'
-- Select: ['__purchasing_customers']
-- Select: ['__purchasing_customers']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  COUNT(DISTINCT customer_id) AS purchasing_customers
FROM "benchmark"."main"."orders" orders_src_10000
