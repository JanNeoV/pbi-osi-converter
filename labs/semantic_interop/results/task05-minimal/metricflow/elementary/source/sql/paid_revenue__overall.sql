-- Read Elements From Semantic Model 'orders'
-- Metric Time Dimension 'session_date'
-- Select: ['__paid_revenue']
-- Select: ['__paid_revenue']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  SUM(amount_eur) AS paid_revenue
FROM "benchmark"."main"."orders" orders_src_10000
