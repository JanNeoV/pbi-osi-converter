SELECT p.category, SUM(i.amount) AS revenue
FROM raw_order_items i LEFT JOIN raw_products p ON i.product_id = p.product_id
GROUP BY p.category
