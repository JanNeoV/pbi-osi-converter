SELECT SUM(i.amount) AS completed_revenue FROM raw_order_items i
JOIN raw_orders o ON i.order_id = o.order_id WHERE o.status = 'completed'
