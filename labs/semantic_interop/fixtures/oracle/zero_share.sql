SELECT p.category, SUM(CASE WHEN o.status = 'completed' THEN i.amount END)
  / NULLIF(SUM(i.amount), 0) AS completed_revenue_share
FROM raw_order_items i JOIN raw_orders o ON i.order_id = o.order_id
JOIN raw_products p ON i.product_id = p.product_id WHERE p.category = 'Zero'
GROUP BY p.category
