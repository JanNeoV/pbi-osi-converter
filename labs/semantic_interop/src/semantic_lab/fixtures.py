from dataclasses import dataclass
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
ROOT = LAB.parents[1]
FIXTURES = LAB / "fixtures"
OSSIE_COMMIT = "6ebc5e5612467dac405985f44ea078b39c891bee"


@dataclass(frozen=True)
class Case:
    id: str
    metric: str
    groups: tuple[str, ...] = ()
    where: tuple[str, ...] = ()
    # Explicit alias map, not positional renaming of arbitrary compiler output.
    aliases: tuple[tuple[str, str], ...] = ()


CASES = (
    Case("total_revenue", "revenue"),
    Case("order_count", "order_count"),
    Case("average_order_value", "average_order_value"),
    Case("revenue_by_category", "revenue", ("product__category",), aliases=(("product__category", "category"),)),
    Case("revenue_over_time", "revenue", ("metric_time__day",), aliases=(("metric_time__day", "order_date"),)),
    Case("completed_revenue", "completed_revenue"),
    Case("completed_revenue_share", "completed_revenue_share"),
    Case("purchasing_customers", "purchasing_customers"),
    Case("zero_share", "completed_revenue_share", ("product__category",),
         ("{{ Dimension('product__category') }} = 'Zero'",), (("product__category", "category"),)),
    Case("year_boundary", "revenue", where=("{{ TimeDimension('metric_time', 'DAY') }} >= '2025-12-31' AND {{ TimeDimension('metric_time', 'DAY') }} < '2026-01-02'",)),
    Case("empty_revenue", "revenue", where=("{{ TimeDimension('metric_time', 'DAY') }} < '2020-01-01'",)),
    Case("empty_orders", "order_count", where=("{{ TimeDimension('metric_time', 'DAY') }} < '2020-01-01'",)),
    Case("empty_customers", "purchasing_customers", where=("{{ TimeDimension('metric_time', 'DAY') }} < '2020-01-01'",)),
    Case("empty_aov", "average_order_value", where=("{{ TimeDimension('metric_time', 'DAY') }} < '2020-01-01'",)),
)


def load_data(connection):
    schemas = {
        "customers": {"customer_id": "INTEGER", "name": "VARCHAR"},
        "products": {"product_id": "INTEGER", "category": "VARCHAR"},
        "orders": {"order_id": "INTEGER", "customer_id": "INTEGER", "order_date": "DATE", "ship_date": "DATE", "status": "VARCHAR"},
        "order_items": {"item_id": "INTEGER", "order_id": "INTEGER", "product_id": "INTEGER", "amount": "DECIMAL(18,2)"},
    }
    for table, columns in schemas.items():
        connection.execute(
            f"CREATE TABLE raw_{table} AS SELECT * FROM read_csv(?, columns=?, header=true, nullstr='')",
            [str(FIXTURES / "data" / f"{table}.csv"), columns],
        )
    calendar_sql = (FIXTURES / "data/calendar.sql").read_text()
    connection.execute("CREATE TABLE raw_calendar AS " + calendar_sql)


def oracle(connection, case):
    sql = (FIXTURES / "oracle" / f"{case.id}.sql").read_text()
    result = connection.execute(sql)
    return [col[0] for col in result.description], result.fetchall(), sql
