-- DuckDB only. Run from repository root. These read the unchanged Task 03 CSVs.
-- This independent oracle is NOT Databricks runtime evidence or a converter.
CREATE TEMP VIEW raw_sessions AS SELECT * FROM read_csv(
  'labs/semantic_interop/fixtures/conversion_demo/data/sessions.csv',
  header=true, columns={'session_id':'BIGINT','session_date':'DATE',
  'sales_channel':'VARCHAR','is_eligible':'BOOLEAN','exclusion_reason':'VARCHAR'});
CREATE TEMP VIEW raw_orders AS SELECT * FROM read_csv(
  'labs/semantic_interop/fixtures/conversion_demo/data/orders.csv',
  header=true, columns={'order_id':'BIGINT','session_id':'BIGINT','order_date':'DATE',
  'amount_eur':'DECIMAL(18,2)','order_status':'VARCHAR'});

-- Independent numerator and denominator: the revenue edit must not change rate.
SELECT COUNT(DISTINCT CASE WHEN o.order_status = 'paid' THEN s.session_id END)::DOUBLE
       / NULLIF(COUNT(DISTINCT s.session_id), 0) AS conversion_rate,
       SUM(CASE WHEN o.order_status = 'paid' THEN o.amount_eur END) AS baseline_paid_revenue,
       SUM(CASE WHEN o.order_status IN ('paid', 'refunded') THEN o.amount_eur END) AS include_refunded_revenue
FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id
  AND o.order_date < DATE '2026-03-01'
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01';

SELECT s.sales_channel,
       COUNT(DISTINCT CASE WHEN o.order_status = 'paid' THEN s.session_id END)::DOUBLE
       / NULLIF(COUNT(DISTINCT s.session_id), 0) AS conversion_rate,
       SUM(CASE WHEN o.order_status = 'paid' THEN o.amount_eur END) AS baseline_paid_revenue,
       SUM(CASE WHEN o.order_status IN ('paid', 'refunded') THEN o.amount_eur END) AS include_refunded_revenue
FROM raw_sessions s LEFT JOIN raw_orders o ON s.session_id = o.session_id
  AND o.order_date < DATE '2026-03-01'
WHERE s.is_eligible AND s.session_date >= DATE '2026-01-01'
  AND s.session_date < DATE '2026-03-01'
GROUP BY s.sales_channel ORDER BY s.sales_channel;
