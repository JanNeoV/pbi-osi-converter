SELECT SUM(i.amount) AS revenue FROM raw_order_items i
JOIN raw_orders o ON i.order_id = o.order_id WHERE o.order_date < DATE '2020-01-01'
