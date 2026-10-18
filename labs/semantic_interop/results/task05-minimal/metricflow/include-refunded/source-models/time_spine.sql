select cast(d as date) as date_day from generate_series(DATE '2026-01-01', DATE '2026-02-28', INTERVAL 1 DAY) t(d)
