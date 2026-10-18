-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  CAST(MAX(subq_5.revenue) AS DOUBLE) / CAST(NULLIF(MAX(subq_11.order_count), 0) AS DOUBLE) AS average_order_value
FROM (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__revenue']
  -- Select: ['__revenue']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(amount) AS revenue
  FROM "benchmark"."main"."items" items_src_10000
) subq_5
CROSS JOIN (
  -- Read Elements From Semantic Model 'orders'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__order_count']
  -- Select: ['__order_count']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    COUNT(DISTINCT order_id) AS order_count
  FROM "benchmark"."main"."orders" orders_src_10000
) subq_11
