-- Deliberate aggregation substitution, not output of any converter.
SELECT SUM(customer_id) AS purchasing_customers FROM raw_orders
