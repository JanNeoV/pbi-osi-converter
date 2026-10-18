-- Joining order totals back to item rows repeats totals once per matching item.
WITH totals AS (SELECT order_id, SUM(amount) AS amount FROM raw_order_items GROUP BY order_id)
SELECT SUM(t.amount) AS revenue FROM totals t JOIN raw_order_items i ON t.order_id = i.order_id
