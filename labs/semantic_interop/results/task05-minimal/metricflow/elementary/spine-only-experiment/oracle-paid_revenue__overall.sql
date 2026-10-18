SELECT SUM(amount_eur) AS paid_revenue FROM raw_orders WHERE order_date < DATE '2026-03-01'
