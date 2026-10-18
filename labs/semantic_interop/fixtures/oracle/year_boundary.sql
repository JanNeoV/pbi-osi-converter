SELECT SUM(i.amount) AS revenue FROM raw_order_items i
JOIN raw_orders o ON i.order_id = o.order_id
WHERE o.order_date >= DATE '2025-12-31' AND o.order_date < DATE '2026-01-02'
