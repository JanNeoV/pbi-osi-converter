-- Both components ignore the requested channel: equivalent teaching intent to
-- removing channel context. This SQL is not DAX execution.
SELECT channels.sales_channel, totals.conversion_rate
FROM (SELECT DISTINCT sales_channel FROM raw_sessions) channels CROSS JOIN (
  SELECT COUNT(DISTINCT CASE WHEN o.order_status = 'paid' THEN s.session_id END)::DOUBLE /
    NULLIF(COUNT(DISTINCT s.session_id), 0) AS conversion_rate
  FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id AND o.order_date < DATE '2026-03-01'
  WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01' AND s.session_date < DATE '2026-03-01'
) totals
