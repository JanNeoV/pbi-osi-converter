SELECT  COUNT(DISTINCT s.session_id) AS eligible_sessions
FROM raw_sessions s
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01' AND (s.session_date < DATE '2020-01-01')

