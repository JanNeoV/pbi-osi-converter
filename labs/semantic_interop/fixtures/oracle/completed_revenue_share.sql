SELECT SUM(CASE WHEN o.status = 'completed' THEN i.amount END)
  / NULLIF(SUM(i.amount), 0) AS completed_revenue_share
FROM raw_order_items i JOIN raw_orders o ON i.order_id = o.order_id
