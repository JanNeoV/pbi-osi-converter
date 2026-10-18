SELECT AVG(rate) AS conversion_rate FROM (
  SELECT s.sales_channel, COUNT(DISTINCT CASE WHEN o.order_status = 'paid' THEN s.session_id END)::DOUBLE /
    NULLIF(COUNT(DISTINCT s.session_id), 0) AS rate
  FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id AND o.order_date < DATE '2026-03-01'
  WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01' AND s.session_date < DATE '2026-03-01'
  GROUP BY s.sales_channel
)
