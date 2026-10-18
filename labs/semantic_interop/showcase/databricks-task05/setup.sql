-- OPTIONAL Databricks runtime setup, NOT_EXECUTED here.
-- Replace demo.semantic_interop with a NEW, authorized Unity Catalog schema.
-- SQL embeds exactly the existing Task 03 CSV rows. No external credentials.
-- CREATE TABLE intentionally fails if a table exists; do not use OR REPLACE.
CREATE TABLE demo.semantic_interop.sessions USING DELTA AS
SELECT CAST(session_id AS BIGINT) AS session_id,
       CAST(session_date AS DATE) AS session_date,
       CAST(sales_channel AS STRING) AS sales_channel,
       CAST(is_eligible AS BOOLEAN) AS is_eligible,
       CAST(exclusion_reason AS STRING) AS exclusion_reason
FROM VALUES
  (1, '2026-01-05', 'Direct', true, NULL),
  (2, '2026-01-06', 'Direct', true, NULL),
  (3, '2026-01-20', 'Direct', true, NULL),
  (4, '2026-02-01', 'Direct', true, NULL),
  (5, '2026-02-02', 'Direct', true, NULL),
  (6, '2026-02-10', 'Direct', true, NULL),
  (7, '2026-01-07', 'Indirect', true, NULL),
  (8, '2026-01-21', 'Indirect', true, NULL),
  (9, '2026-02-03', 'Indirect', true, NULL),
  (10, '2026-02-05', 'Indirect', true, NULL),
  (11, '2026-01-08', 'Direct', false, 'bot'),
  (12, '2026-02-06', 'Indirect', false, 'internal')
AS fixture(session_id, session_date, sales_channel, is_eligible, exclusion_reason);

CREATE TABLE demo.semantic_interop.orders USING DELTA AS
SELECT CAST(order_id AS BIGINT) AS order_id,
       CAST(session_id AS BIGINT) AS session_id,
       CAST(order_date AS DATE) AS order_date,
       CAST(amount_eur AS DECIMAL(18,2)) AS amount_eur,
       CAST(order_status AS STRING) AS order_status
FROM VALUES
  (1, 1, '2026-01-05', 100, 'paid'),
  (2, 2, '2026-02-01', 80, 'paid'),
  (3, 4, '2026-02-02', 120, 'paid'),
  (4, 7, '2026-01-07', 90, 'paid'),
  (5, 5, '2026-02-02', 20, 'cancelled'),
  (6, 9, '2026-02-03', 50, 'refunded')
AS fixture(order_id, session_id, order_date, amount_eur, order_status);

SELECT COUNT(*) AS session_rows FROM demo.semantic_interop.sessions; -- 12
SELECT COUNT(*) AS order_rows FROM demo.semantic_interop.orders; -- 6
-- Then create the generated Metric View using DATABRICKS_HANDOFF.md instructions.
