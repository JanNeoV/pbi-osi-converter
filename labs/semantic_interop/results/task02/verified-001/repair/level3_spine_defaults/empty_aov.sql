-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  CAST(MAX(subq_6.average_order_value__numerator) AS DOUBLE) / CAST(NULLIF(MAX(subq_13.average_order_value__denominator), 0) AS DOUBLE) AS average_order_value
FROM (
  -- Constrain Output with WHERE
  -- Select: ['__average_order_value__numerator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(average_order_value__numerator) AS average_order_value__numerator
  FROM (
    -- Read Elements From Semantic Model 'items'
    -- Metric Time Dimension 'order_date'
    -- Select: ['__average_order_value__numerator', 'metric_time__day']
    SELECT
      DATE_TRUNC('day', order_date) AS metric_time__day
      , amount AS average_order_value__numerator
    FROM "benchmark"."main"."items" items_src_10000
  ) subq_2
  WHERE metric_time__day < '2020-01-01'
) subq_6
CROSS JOIN (
  -- Constrain Output with WHERE
  -- Select: ['__average_order_value__denominator']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    COUNT(DISTINCT average_order_value__denominator) AS average_order_value__denominator
  FROM (
    -- Read Elements From Semantic Model 'orders'
    -- Metric Time Dimension 'order_date'
    -- Select: ['__average_order_value__denominator', 'metric_time__day']
    SELECT
      DATE_TRUNC('day', order_date) AS metric_time__day
      , order_id AS average_order_value__denominator
    FROM "benchmark"."main"."orders" orders_src_10000
  ) subq_9
  WHERE metric_time__day < '2020-01-01'
) subq_13
