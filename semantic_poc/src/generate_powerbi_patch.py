from __future__ import annotations

from .models import (
    DBT_OUTPUT,
    POWERBI_OUTPUT,
    POWERBI_PATCH_OUTPUT,
    generate_powerbi_patch,
    load_json,
    load_normalized_dbt_semantics,
    parse_tmdl_definition,
    write_json,
)


def main() -> None:
    dbt_semantics = load_json(DBT_OUTPUT) if DBT_OUTPUT.exists() else load_normalized_dbt_semantics()
    powerbi = load_json(POWERBI_OUTPUT) if POWERBI_OUTPUT.exists() else parse_tmdl_definition()
    patch = generate_powerbi_patch(dbt_semantics, powerbi)
    write_json(POWERBI_PATCH_OUTPUT, patch, generated=True, canonical_source=dbt_semantics["canonical_source"])
    print(f"Wrote {POWERBI_PATCH_OUTPUT}")


if __name__ == "__main__":
    main()
