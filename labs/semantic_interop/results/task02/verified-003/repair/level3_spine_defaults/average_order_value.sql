-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  CAST(MAX(subq_5.average_order_value__numerator) AS DOUBLE) / CAST(NULLIF(MAX(subq_11.average_order_value__denominator), 0) AS DOUBLE) AS average_order_value
FROM (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__average_order_value__numerator']
  -- Select: ['__average_order_value__numerator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(amount) AS average_order_value__numerator
  FROM "benchmark"."main"."items" items_src_10000
) subq_5
CROSS JOIN (
  -- Read Elements From Semantic Model 'orders'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__average_order_value__denominator']
  -- Select: ['__average_order_value__denominator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    COUNT(DISTINCT order_id) AS average_order_value__denominator
  FROM "benchmark"."main"."orders" orders_src_10000
) subq_11
