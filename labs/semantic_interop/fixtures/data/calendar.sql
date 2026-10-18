-- Fixed inclusive calendar, including leap day and the alternative shipping dates.
SELECT CAST(day AS DATE) AS date_day
FROM generate_series(DATE '2024-02-28', DATE '2026-01-03', INTERVAL 1 DAY) AS t(day)
