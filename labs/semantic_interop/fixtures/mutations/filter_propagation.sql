-- Filtering a disconnected set of orders does not filter the item relation.
SELECT SUM(i.amount) AS completed_revenue FROM raw_order_items i
WHERE EXISTS (SELECT 1 FROM raw_orders o WHERE o.status = 'completed')
