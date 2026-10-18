-- Compute Metrics via Expressions
-- Write to DataTable
WITH sma_10001_cte AS (
  -- Read Elements From Semantic Model 'items'
  -- Metric Time Dimension 'order_date'
  SELECT
    product_id AS product
    , status AS item__status
    , amount AS __revenue
    , amount AS __completed_revenue
  FROM "benchmark"."main"."items" items_src_10000
)

, rss_10003_cte AS (
  -- Read Elements From Semantic Model 'products'
  SELECT
    category
    , product_id AS product
  FROM "benchmark"."main"."products" products_src_10000
)

SELECT
  product__category AS product__category
  , CAST(completed_revenue AS DOUBLE) / CAST(NULLIF(revenue, 0) AS DOUBLE) AS completed_revenue_share
FROM (
  -- Combine Aggregated Outputs
  SELECT
    COALESCE(subq_9.product__category, subq_18.product__category) AS product__category
    , MAX(subq_9.completed_revenue) AS completed_revenue
    , MAX(subq_18.revenue) AS revenue
  FROM (
    -- Constrain Output with WHERE
    -- Select: ['__completed_revenue', 'product__category']
    -- Aggregate Inputs for Simple Metrics
    -- Compute Metrics via Expressions
    SELECT
      product__category
      , SUM(completed_revenue) AS completed_revenue
    FROM (
      -- Join Standard Outputs
      -- Select: ['__completed_revenue', 'product__category', 'item__status']
      SELECT
        sma_10001_cte.item__status AS item__status
        , rss_10003_cte.category AS product__category
        , sma_10001_cte.__completed_revenue AS completed_revenue
      FROM sma_10001_cte
      LEFT OUTER JOIN
        rss_10003_cte
      ON
        sma_10001_cte.product = rss_10003_cte.product
    ) subq_5
    WHERE (item__status = 'completed') AND (product__category = 'Zero')
    GROUP BY
      product__category
  ) subq_9
  FULL OUTER JOIN (
    -- Constrain Output with WHERE
    -- Select: ['__revenue', 'product__category']
    -- Aggregate Inputs for Simple Metrics
    -- Compute Metrics via Expressions
    SELECT
      product__category
      , SUM(revenue) AS revenue
    FROM (
      -- Join Standard Outputs
      -- Select: ['__revenue', 'product__category']
      SELECT
        rss_10003_cte.category AS product__category
        , sma_10001_cte.__revenue AS revenue
      FROM sma_10001_cte
      LEFT OUTER JOIN
        rss_10003_cte
      ON
        sma_10001_cte.product = rss_10003_cte.product
    ) subq_14
    WHERE product__category = 'Zero'
    GROUP BY
      product__category
  ) subq_18
  ON
    subq_9.product__category = subq_18.product__category
  GROUP BY
    COALESCE(subq_9.product__category, subq_18.product__category)
) subq_19
