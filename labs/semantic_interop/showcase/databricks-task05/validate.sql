-- Prospective Databricks SQL: run only after creating the named Metric View.
-- Change the namespace and view name to the separately captured runtime lane.
-- Baseline expected: 0.4, 390.00. Include-refunded expected: 0.4, 440.00.
SELECT MEASURE(conversion_rate) AS conversion_rate,
       MEASURE(paid_revenue) AS paid_revenue_eur
FROM demo.semantic_interop.task05_baseline;

-- Baseline: Direct 0.5 / 300.00; Indirect 0.25 / 90.00.
-- Include-refunded: Direct 0.5 / 300.00; Indirect 0.25 / 140.00.
SELECT sales_channel, MEASURE(conversion_rate) AS conversion_rate,
       MEASURE(paid_revenue) AS paid_revenue_eur
FROM demo.semantic_interop.task05_baseline
GROUP BY sales_channel ORDER BY sales_channel;
