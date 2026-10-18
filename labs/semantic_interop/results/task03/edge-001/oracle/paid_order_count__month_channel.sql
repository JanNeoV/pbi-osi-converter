SELECT CAST(DATE_TRUNC('month', s.session_date) AS DATE) AS session_month, s.sales_channel, COUNT(DISTINCT o.order_id) AS paid_order_count
FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id
  AND o.order_status = 'paid' AND o.order_date < DATE '2026-03-01'
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01' 
GROUP BY 1, 2
