SELECT {dimensions} COUNT(DISTINCT CASE WHEN EXISTS (
    SELECT 1 FROM raw_orders o WHERE o.session_id = s.session_id
      AND o.order_status = 'paid' AND o.order_date < DATE '2026-03-01'
  ) THEN s.session_id END)::DOUBLE / NULLIF(COUNT(DISTINCT s.session_id), 0) AS conversion_rate
FROM raw_sessions s
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01' {cohort_filter}
{group_by}
