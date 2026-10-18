SELECT CAST(DATE_TRUNC('month', s.session_date) AS DATE) AS session_month, COUNT(DISTINCT s.session_id) AS eligible_sessions
FROM raw_sessions s
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01' 
GROUP BY 1
