-- MANUALLY_AUTHORED_CANDIDATE target queries. Execution: NOT_EXECUTED.
-- Assumes a future contributor creates demo.semantic_interop.conversion_metrics
-- from metric_view.yaml and uploads the exact canonical CSV rows with explicit
-- BIGINT / DATE / STRING / BOOLEAN / DECIMAL(18,2) types. No deployment is performed.

-- Overall expected: 10, 4, 0.4, 390.00, 4, 97.5.
SELECT MEASURE(eligible_sessions), MEASURE(converted_sessions),
       MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics;

-- Direct: 6, 3, 0.5, 300.00. Indirect: 4, 1, 0.25, 90.00.
SELECT sales_channel, MEASURE(eligible_sessions), MEASURE(converted_sessions),
       MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics
GROUP BY sales_channel
ORDER BY sales_channel;

-- Session cohorts: January 5, 3, 0.6, 270.00; February 5, 1, 0.2, 120.00.
SELECT session_month, MEASURE(eligible_sessions), MEASURE(converted_sessions),
       MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics
GROUP BY session_month
ORDER BY session_month;

-- Four channel-month cohorts, including February Indirect with 2 sessions and 0 conversions.
SELECT session_month, sales_channel, MEASURE(eligible_sessions),
       MEASURE(converted_sessions), MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics
GROUP BY session_month, sales_channel
ORDER BY session_month, sales_channel;

-- Empty scalar cohort: counts 0; revenue, conversion rate and AOV NULL.
SELECT MEASURE(eligible_sessions), MEASURE(converted_sessions),
       MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics
WHERE session_month < DATE '2020-01-01';

-- Nonempty zero-conversion cohort: 2, 0, 0.0, NULL, 0, NULL.
SELECT MEASURE(eligible_sessions), MEASURE(converted_sessions),
       MEASURE(conversion_rate), MEASURE(paid_revenue),
       MEASURE(paid_order_count), MEASURE(average_paid_order_value)
FROM demo.semantic_interop.conversion_metrics
WHERE session_month = DATE '2026-02-01' AND sales_channel = 'Indirect';

-- After adding the optional extra paid-order CSV row in a separate experiment:
-- expected overall 10, 4, 0.4, 420.00, 5, 84.0. Do not overwrite canonical data.
