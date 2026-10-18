SELECT COUNT(DISTINCT customer_id) AS purchasing_customers FROM raw_orders WHERE order_date < DATE '2020-01-01'
