-- Constrain Output with WHERE
-- Select: ['__purchasing_customers']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  COUNT(DISTINCT purchasing_customers) AS purchasing_customers
FROM (
  -- Read Elements From Semantic Model 'orders'
  -- Metric Time Dimension 'order_date'
  -- Select: ['__purchasing_customers', 'metric_time__day']
  SELECT
    DATE_TRUNC('day', order_date) AS metric_time__day
    , customer_id AS purchasing_customers
  FROM "benchmark"."main"."orders" orders_src_10000
) subq_2
WHERE metric_time__day < '2020-01-01'
