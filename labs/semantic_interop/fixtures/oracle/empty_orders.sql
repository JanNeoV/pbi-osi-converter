SELECT COUNT(DISTINCT order_id) AS order_count FROM raw_orders WHERE order_date < DATE '2020-01-01'
