from __future__ import annotations

from .models import (
    COMPATIBILITY_OUTPUT,
    DBT_OUTPUT,
    POWERBI_OUTPUT,
    SNOWFLAKE_ENVIRONMENT,
    SNOWFLAKE_OUTPUT,
    build_snowflake_semantic_view,
    compare_semantics,
    load_json,
    load_normalized_dbt_semantics,
    load_yaml,
    parse_tmdl_definition,
    render_compatibility_markdown,
)


def main() -> None:
    dbt_semantics = load_json(DBT_OUTPUT) if DBT_OUTPUT.exists() else load_normalized_dbt_semantics()
    powerbi = load_json(POWERBI_OUTPUT) if POWERBI_OUTPUT.exists() else parse_tmdl_definition()
    if SNOWFLAKE_OUTPUT.exists():
        snowflake = load_yaml(SNOWFLAKE_OUTPUT)
    else:
        snowflake = build_snowflake_semantic_view(dbt_semantics, load_yaml(SNOWFLAKE_ENVIRONMENT))
    comparison = compare_semantics(dbt_semantics, powerbi, snowflake)
    COMPATIBILITY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    COMPATIBILITY_OUTPUT.write_text(
        render_compatibility_markdown(
            comparison,
            dbt_semantics["canonical_source"],
            dbt_semantics["compiled_source"],
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {COMPATIBILITY_OUTPUT}")


if __name__ == "__main__":
    main()
