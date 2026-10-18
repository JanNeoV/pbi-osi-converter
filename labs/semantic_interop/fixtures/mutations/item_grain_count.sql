-- An item row is not an order. Also omits the order with no items.
SELECT COUNT(*) AS order_count FROM raw_order_items
