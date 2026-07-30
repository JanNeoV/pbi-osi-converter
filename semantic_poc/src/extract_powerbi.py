from __future__ import annotations

from .models import CANONICAL_SOURCE, POWERBI_OUTPUT, parse_tmdl_definition, write_json


def main() -> None:
    data = parse_tmdl_definition()
    write_json(POWERBI_OUTPUT, data, generated=True, canonical_source=CANONICAL_SOURCE)
    print(f"Wrote {POWERBI_OUTPUT}")


if __name__ == "__main__":
    main()
