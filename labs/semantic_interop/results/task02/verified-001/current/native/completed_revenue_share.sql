-- Combine Aggregated Outputs
-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10001_cte AS (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  SELECT
    status AS item__status
    , amount AS __revenue
    , amount AS __completed_revenue
  FROM "benchmark"."main"."items" items_src_10000
)

SELECT
  CAST(MAX(subq_6.completed_revenue) AS DOUBLE) / CAST(NULLIF(MAX(subq_11.revenue), 0) AS DOUBLE) AS completed_revenue_share
FROM (
  -- Constrain Output with WHERE
  -- Select: ['__completed_revenue']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(completed_revenue) AS completed_revenue
  FROM (
    -- Read From CTE For node_id=sma_10001
    -- Select: ['__completed_revenue', 'item__status']
    SELECT
      item__status
      , __completed_revenue AS completed_revenue
    FROM sma_10001_cte
  ) subq_2
  WHERE item__status = 'completed'
) subq_6
CROSS JOIN (
  -- Read From CTE For node_id=sma_10001
  -- Select: ['__revenue']
  -- Select: ['__revenue']
  -- Aggregate Inputs for Simple Metrics
  -- Compute Metrics via Expressions
  SELECT
    SUM(__revenue) AS revenue
  FROM sma_10001_cte
) subq_11
