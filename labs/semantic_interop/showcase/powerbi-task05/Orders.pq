let
    Rows = #table(
        type table [order_id = Int64.Type, session_id = Int64.Type, order_date = date, amount_eur = Currency.Type, order_status = text],
        {
        {1, 1, #date(2026, 1, 5), 100, "paid"},
        {2, 2, #date(2026, 2, 1), 80, "paid"},
        {3, 4, #date(2026, 2, 2), 120, "paid"},
        {4, 7, #date(2026, 1, 7), 90, "paid"},
        {5, 5, #date(2026, 2, 2), 20, "cancelled"},
        {6, 9, #date(2026, 2, 3), 50, "refunded"}
        }
    )
in
    Rows
