SELECT (SELECT SUM(amount) FROM raw_order_items)
  / NULLIF((SELECT COUNT(DISTINCT order_id) FROM raw_orders), 0) AS average_order_value
