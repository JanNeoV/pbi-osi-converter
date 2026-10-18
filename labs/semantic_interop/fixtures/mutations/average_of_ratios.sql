-- Equal weighting per date differs from a ratio of totals.
WITH daily AS (
 SELECT o.order_date, SUM(CASE WHEN o.status = 'completed' THEN i.amount ELSE 0 END) AS completed,
   SUM(i.amount) AS total
 FROM raw_order_items i JOIN raw_orders o ON i.order_id = o.order_id GROUP BY o.order_date
)
SELECT AVG(completed / NULLIF(total, 0)) AS completed_revenue_share FROM daily
