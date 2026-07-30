from __future__ import annotations

from .models import DBT_OUTPUT, load_normalized_dbt_semantics, write_json


def main() -> None:
    data = load_normalized_dbt_semantics()
    write_json(DBT_OUTPUT, data, generated=True, canonical_source=data["canonical_source"])
    print(f"Wrote {DBT_OUTPUT}")


if __name__ == "__main__":
    main()
