SELECT s.sales_channel, SUM(o.amount_eur) AS paid_revenue
FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id
  AND o.order_status = 'paid' AND o.order_date < DATE '2026-03-01'
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01' 
GROUP BY 1
