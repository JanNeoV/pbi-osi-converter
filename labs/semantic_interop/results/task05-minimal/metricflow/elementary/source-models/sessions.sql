-- Fixed session cohort, independent of order occurrence.
select * from raw_sessions
where session_date >= DATE '2026-01-01' and session_date < DATE '2026-03-01'
