from __future__ import annotations

from .models import (
    DBT_OUTPUT,
    SNOWFLAKE_ENVIRONMENT,
    SNOWFLAKE_OUTPUT,
    build_snowflake_semantic_view,
    load_json,
    load_normalized_dbt_semantics,
    load_yaml,
    write_yaml,
)


def main() -> None:
    dbt_semantics = load_json(DBT_OUTPUT) if DBT_OUTPUT.exists() else load_normalized_dbt_semantics()
    environment = load_yaml(SNOWFLAKE_ENVIRONMENT)
    view = build_snowflake_semantic_view(dbt_semantics, environment)
    write_yaml(SNOWFLAKE_OUTPUT, view, generated=True, canonical_source=dbt_semantics["canonical_source"])
    print(f"Wrote {SNOWFLAKE_OUTPUT}")


if __name__ == "__main__":
    main()
