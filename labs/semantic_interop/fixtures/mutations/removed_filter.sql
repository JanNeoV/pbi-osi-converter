-- Intentionally missing the completed-order restriction.
SELECT SUM(amount) AS completed_revenue FROM raw_order_items
