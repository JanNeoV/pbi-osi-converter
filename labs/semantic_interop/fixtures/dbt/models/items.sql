select i.*, o.order_date, o.status
from raw_order_items i
join raw_orders o on i.order_id = o.order_id
