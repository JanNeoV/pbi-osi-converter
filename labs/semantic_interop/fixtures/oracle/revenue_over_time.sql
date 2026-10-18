SELECT o.order_date, SUM(i.amount) AS revenue
FROM raw_order_items i JOIN raw_orders o ON i.order_id = o.order_id
GROUP BY o.order_date
