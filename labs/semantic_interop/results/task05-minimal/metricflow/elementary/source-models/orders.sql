-- Attach originating session date for cohort reporting; no eligibility/status filtering here.
select o.*, s.session_date
from raw_orders o join raw_sessions s on o.session_id = s.session_id
where o.order_date < DATE '2026-03-01'
