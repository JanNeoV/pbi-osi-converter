from __future__ import annotations

from .models import load_normalized_dbt_semantics, validate_dbt_semantics


def main() -> None:
    data = load_normalized_dbt_semantics()
    errors = validate_dbt_semantics(data)
    if errors:
        raise SystemExit("Contract validation failed:\n- " + "\n- ".join(errors))
    print("Contract validation passed.")


if __name__ == "__main__":
    main()
