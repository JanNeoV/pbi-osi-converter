-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  CAST(MAX(subq_5.completed_revenue_share__numerator) AS DOUBLE) / CAST(NULLIF(MAX(subq_11.completed_revenue_share__denominator), 0) AS DOUBLE) AS completed_revenue_share
FROM (
  -- Read Elements From Semantic Model 'orders'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__completed_revenue_share__numerator']
  -- Select: ['__completed_revenue_share__numerator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(SUM(CASE WHEN item__status = 'completed' THEN items.amount END)) AS completed_revenue_share__numerator
  FROM "benchmark"."main"."orders" orders_src_10000
) subq_5
CROSS JOIN (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__completed_revenue_share__denominator']
  -- Select: ['__completed_revenue_share__denominator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(amount) AS completed_revenue_share__denominator
  FROM "benchmark"."main"."items" items_src_10000
) subq_11
