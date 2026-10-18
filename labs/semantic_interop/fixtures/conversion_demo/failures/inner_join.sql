SELECT COUNT(DISTINCT CASE WHEN o.order_status = 'paid' THEN s.session_id END)::DOUBLE /
  NULLIF(COUNT(DISTINCT s.session_id), 0) AS conversion_rate
FROM raw_sessions s JOIN raw_orders o ON s.session_id = o.session_id AND o.order_date < DATE '2026-03-01'
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01' AND s.session_date < DATE '2026-03-01'
