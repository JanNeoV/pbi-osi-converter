-- Join Standard Outputs
-- Select: ['__revenue', 'product__category']
-- Select: ['__revenue', 'product__category']
-- Aggregate Inputs for Simple Metrics
-- Compute Metrics via Expressions
-- Write to DataTable
SELECT
  products_src_10000.category AS product__category
  , SUM(items_src_10000.amount) AS revenue
FROM "benchmark"."main"."items" items_src_10000
LEFT OUTER JOIN
  "benchmark"."main"."products" products_src_10000
ON
  items_src_10000.product_id = products_src_10000.product_id
GROUP BY
  products_src_10000.category
