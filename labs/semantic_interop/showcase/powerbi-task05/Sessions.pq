let
    Rows = #table(
        type table [session_id = Int64.Type, session_date = date, sales_channel = text, is_eligible = logical, exclusion_reason = nullable text],
        {
        {1, #date(2026, 1, 5), "Direct", true, null},
        {2, #date(2026, 1, 6), "Direct", true, null},
        {3, #date(2026, 1, 20), "Direct", true, null},
        {4, #date(2026, 2, 1), "Direct", true, null},
        {5, #date(2026, 2, 2), "Direct", true, null},
        {6, #date(2026, 2, 10), "Direct", true, null},
        {7, #date(2026, 1, 7), "Indirect", true, null},
        {8, #date(2026, 1, 21), "Indirect", true, null},
        {9, #date(2026, 2, 3), "Indirect", true, null},
        {10, #date(2026, 2, 5), "Indirect", true, null},
        {11, #date(2026, 1, 8), "Direct", false, "bot"},
        {12, #date(2026, 2, 6), "Indirect", false, "internal"}
        }
    ),
    WithMonth = Table.AddColumn(Rows, "session_month", each Date.StartOfMonth([session_date]), type date)
in
    WithMonth
