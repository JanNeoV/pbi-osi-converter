SELECT (SELECT SUM(i.amount) FROM raw_order_items i JOIN raw_orders o ON i.order_id = o.order_id
 WHERE o.order_date < DATE '2020-01-01') /
 NULLIF((SELECT COUNT(DISTINCT order_id) FROM raw_orders WHERE order_date < DATE '2020-01-01'), 0)
 AS average_order_value
