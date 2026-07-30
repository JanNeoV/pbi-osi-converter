from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

from .schemas import CreateMetricPattern, RequestFilterOperator, TypedMetricDefinition


class CanonicalPatchError(RuntimeError):
    """Raised when an approved canonical patch cannot be applied losslessly."""


_SELECTOR = re.compile(
    r"^(metrics|semantic_models)\[([A-Za-z_][A-Za-z0-9_]*)\]\."
    r"(?:(measures)\[([A-Za-z_][A-Za-z0-9_]*)\]\.)?"
    r"([A-Za-z_][A-Za-z0-9_.]*)$"
)
_NAMED_ITEM = re.compile(r"^(?P<indent>\s*)-\s+name:\s*(?P<value>.+?)\s*$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _section(lines: list[str], name: str) -> tuple[int, int]:
    matches = [index for index, line in enumerate(lines) if line == f"{name}:"]
    if len(matches) != 1:
        raise CanonicalPatchError(f"Expected exactly one top-level `{name}` section.")
    start = matches[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "#")):
            end = index
            break
    return start, end


def _parse_scalar(token: str) -> Any:
    try:
        return yaml.safe_load("value: " + token)["value"]
    except (TypeError, yaml.YAMLError) as exc:
        raise CanonicalPatchError(f"Unsupported YAML scalar: {token!r}") from exc


def _split_comment(value: str) -> tuple[str, str]:
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote == "'" and char == "'" and index + 1 < len(value) and value[index + 1] == "'":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = None if quote == char else (char if quote is None else quote)
        elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip(), value[index - 1 :]
        index += 1
    return value.rstrip(), value[len(value) :]


def _named_block(lines: list[str], start: int, end: int, indent: int, name: str) -> tuple[int, int]:
    matches: list[int] = []
    for index in range(start, end):
        match = _NAMED_ITEM.match(lines[index])
        if not match or len(match.group("indent")) != indent:
            continue
        token, _comment = _split_comment(match.group("value"))
        if _parse_scalar(token) == name:
            matches.append(index)
    if len(matches) != 1:
        raise CanonicalPatchError(f"Expected exactly one named YAML item {name!r}.")
    block_start = matches[0]
    block_end = end
    for index in range(block_start + 1, end):
        line = lines[index]
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) <= indent:
            block_end = index
            break
    return block_start, block_end


def _mapping_block(lines: list[str], start: int, end: int, indent: int, key: str) -> tuple[int, int]:
    pattern = re.compile(rf"^\s{{{indent}}}{re.escape(key)}:\s*$")
    matches = [index for index in range(start, end) if pattern.match(lines[index])]
    if len(matches) != 1:
        raise CanonicalPatchError(f"Expected exactly one mapping block `{key}`.")
    block_start = matches[0] + 1
    block_end = end
    for index in range(block_start, end):
        line = lines[index]
        if line.strip() and not line.lstrip().startswith("#") and _indent(line) <= indent:
            block_end = index
            break
    return block_start, block_end


def _scalar_line(lines: list[str], start: int, end: int, indent: int, key: str) -> tuple[int, str, str, str]:
    pattern = re.compile(rf"^(?P<prefix>\s{{{indent}}}{re.escape(key)}:\s*)(?P<value>.*)$")
    matches: list[tuple[int, re.Match[str]]] = []
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            matches.append((index, match))
    if len(matches) != 1:
        raise CanonicalPatchError(f"Expected exactly one scalar key `{key}`.")
    index, match = matches[0]
    token, comment = _split_comment(match.group("value"))
    if not token or token in {"|", ">"} or token.startswith(("&", "*", "[", "{")):
        raise CanonicalPatchError(f"Scalar `{key}` uses an unsupported YAML representation.")
    return index, match.group("prefix"), token, comment


def _selector_line(lines: list[str], selector: str) -> tuple[int, str, str, str]:
    match = _SELECTOR.fullmatch(selector)
    if not match:
        raise CanonicalPatchError(f"Unsupported canonical selector: {selector}")
    section_name, item_name, collection, nested_name, path = match.groups()
    start, end = _section(lines, section_name)
    item_indent = 2
    start, end = _named_block(lines, start, end, item_indent, item_name)
    property_indent = item_indent + 2
    if collection == "measures":
        start, end = _mapping_block(lines, start + 1, end, property_indent, collection)
        item_indent = property_indent + 2
        start, end = _named_block(lines, start, end, item_indent, nested_name or "")
        property_indent = item_indent + 2
    components = path.split(".")
    for component in components[:-1]:
        start, end = _mapping_block(lines, start + 1, end, property_indent, component)
        property_indent += 2
    return _scalar_line(lines, start + 1, end, property_indent, components[-1])


def _plain_string(value: str) -> bool:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        return False
    if value.casefold() in {"null", "true", "false", "yes", "no", "on", "off", "~"}:
        return False
    if value[0] in "-?:,[]{}#&*!|>'\"%@`" or ": " in value or " #" in value:
        return False
    return True


def render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        if _plain_string(value):
            return value
        return "'" + value.replace("'", "''") + "'"
    raise CanonicalPatchError(f"Unsupported canonical scalar type: {type(value).__name__}")


def _typed_literal(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _typed_metric_objects(name: str, definition: TypedMetricDefinition) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    measure_name = f"{name}_measure"
    measure: dict[str, Any] | None
    if definition.pattern is CreateMetricPattern.RATIO:
        measure = None
        type_name = "ratio"
        type_params = {"numerator": definition.numerator, "denominator": definition.denominator}
    else:
        type_name = "simple"
        type_params = {"measure": measure_name}
        if definition.pattern is CreateMetricPattern.COUNT:
            agg, expression = "sum", "1"
        elif definition.pattern is CreateMetricPattern.COLUMN_COUNT:
            agg, expression = "count", definition.source_field
        elif definition.pattern is CreateMetricPattern.SUM:
            agg, expression = "sum", definition.source_field
        elif definition.pattern is CreateMetricPattern.DISTINCT_COUNT:
            agg, expression = "count_distinct", definition.source_field
        elif definition.pattern is CreateMetricPattern.FILTERED_COUNT:
            if any(item.operator is not RequestFilterOperator.EQ for item in definition.filters):
                raise CanonicalPatchError("Only EQ predicates can be inserted by a typed metric patch.")
            agg = "sum_boolean"
            expression = " AND ".join(
                f"{item.field} = {_typed_literal(item.value)}" for item in definition.filters
            )
        elif definition.pattern is CreateMetricPattern.SCALED_SUM:
            agg, expression = "sum", f"{definition.source_field} / {definition.scale_divisor}"
        else:  # pragma: no cover - the enum makes this unreachable
            raise CanonicalPatchError(f"Unsupported typed metric pattern: {definition.pattern.value}")
        measure = {
            "name": measure_name,
            "description": definition.description,
            "agg": agg,
            "expr": expression,
        }

    power_bi = {
        "table": definition.power_bi_table,
        "measure": definition.power_bi_measure,
        "format_string": definition.power_bi_format_string,
    }
    if definition.power_bi_display_folder is not None:
        power_bi["display_folder"] = definition.power_bi_display_folder
    snowflake: dict[str, Any] = {
        "logical_table": definition.snowflake_logical_table,
        "metric_name": definition.snowflake_metric_name,
    }
    if definition.snowflake_synonyms:
        snowflake["synonyms"] = list(definition.snowflake_synonyms)
    metric = {
        "name": name,
        "label": definition.label,
        "description": definition.description,
        "type": type_name,
        "type_params": type_params,
        "config": {
            "meta": {
                "semantic_contract": {
                    "version": 1,
                    "public": definition.public,
                    "format": definition.semantic_format,
                },
                "power_bi": power_bi,
                "snowflake": snowflake,
            }
        },
    }
    return measure, metric


def _declared_model_fields(model: Mapping[str, Any]) -> set[str]:
    fields: set[str] = set()
    for collection in ("entities", "dimensions"):
        for item in model.get(collection, []) or []:
            if not isinstance(item, Mapping):
                continue
            for key in ("name", "expr"):
                value = item.get(key)
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                    fields.add(value)
    for measure in model.get("measures", []) or []:
        expression = measure.get("expr") if isinstance(measure, Mapping) else None
        if isinstance(expression, str):
            fields.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression))
    return fields - {"AND", "OR", "TRUE", "FALSE"}


def _validate_typed_insertion(parsed: Mapping[str, Any], name: str, definition: TypedMetricDefinition) -> None:
    metrics = [item for item in parsed.get("metrics", []) or [] if isinstance(item, Mapping)]
    if any(item.get("name") == name for item in metrics):
        raise CanonicalPatchError(f"Canonical metric already exists: {name!r}.")
    models = [
        item
        for item in parsed.get("semantic_models", []) or []
        if isinstance(item, Mapping) and item.get("name") == definition.semantic_model
    ]
    if len(models) != 1:
        raise CanonicalPatchError("Typed metric insertion requires one exact existing semantic model.")
    model = models[0]
    logical_tables = (
        (((model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {}).get(
            "snowflake_logical_tables", []
        )
        or []
    )
    if sum(
        1
        for item in logical_tables
        if isinstance(item, Mapping) and item.get("name") == definition.snowflake_logical_table
    ) != 1:
        raise CanonicalPatchError("Typed metric insertion requires one exact existing Snowflake logical table.")
    required_fields = set()
    if definition.source_field:
        required_fields.add(definition.source_field)
    required_fields.update(item.field for item in definition.filters)
    unknown = required_fields - _declared_model_fields(model)
    if unknown:
        raise CanonicalPatchError(
            "Typed metric insertion cannot add or infer semantic fields: " + ", ".join(sorted(unknown))
        )
    if definition.pattern is CreateMetricPattern.RATIO:
        names = {str(item.get("name")) for item in metrics}
        missing = {definition.numerator, definition.denominator} - names
        if missing:
            raise CanonicalPatchError(
                "Typed ratio insertion requires exact existing canonical references: "
                + ", ".join(sorted(value for value in missing if value))
            )
    measure_name = f"{name}_measure"
    existing_measure_names = {
        str(measure.get("name"))
        for candidate in parsed.get("semantic_models", []) or []
        if isinstance(candidate, Mapping)
        for measure in candidate.get("measures", []) or []
        if isinstance(measure, Mapping)
    }
    if definition.pattern is not CreateMetricPattern.RATIO and measure_name in existing_measure_names:
        raise CanonicalPatchError(f"Deterministic measure name already exists: {measure_name!r}.")


def _dump_indented(value: Any, indent: int) -> list[str]:
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    return [(" " * indent) + line if line else line for line in text.splitlines()]


def _insert_typed_metric(lines: list[str], operation: Mapping[str, Any]) -> None:
    if set(operation) != {"operation", "metric_name", "definition"}:
        raise CanonicalPatchError("Typed metric insertion has unexpected fields.")
    name = operation["metric_name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise CanonicalPatchError("Typed metric insertion requires a safe exact metric name.")
    try:
        definition = TypedMetricDefinition.from_dict(operation["definition"])
    except (TypeError, ValueError) as exc:
        raise CanonicalPatchError(f"Invalid typed metric insertion: {exc}") from exc
    try:
        parsed = yaml.safe_load("\n".join(lines)) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - checked by the caller too
        raise CanonicalPatchError(f"Canonical source is invalid YAML: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise CanonicalPatchError("Canonical source must be a YAML mapping.")
    _validate_typed_insertion(parsed, name, definition)
    measure, metric = _typed_metric_objects(name, definition)
    if measure is not None:
        start, end = _section(lines, "semantic_models")
        model_start, model_end = _named_block(lines, start, end, 2, definition.semantic_model)
        _measure_start, measure_end = _mapping_block(lines, model_start + 1, model_end, 4, "measures")
        lines[measure_end:measure_end] = _dump_indented([measure], 6)
    _metric_start, metric_end = _section(lines, "metrics")
    lines[metric_end:metric_end] = _dump_indented([metric], 2)


def render_candidate(source: bytes, operations: tuple[Mapping[str, Any], ...]) -> bytes:
    if source.startswith(b"\xef\xbb\xbf"):
        raise CanonicalPatchError("Canonical YAML with a BOM is not supported for lossless application.")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalPatchError("Canonical YAML must be UTF-8.") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    had_final_newline = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    changed_lines: set[int] = set()
    for operation in operations:
        if operation.get("operation") == "insert_typed_metric":
            _insert_typed_metric(lines, operation)
            continue
        if set(operation) != {"operation", "selector", "current", "proposed"}:
            raise CanonicalPatchError("Canonical patch operation has unexpected fields.")
        if operation["operation"] != "replace":
            raise CanonicalPatchError("Only fixed-selector replace operations are supported.")
        index, prefix, token, comment = _selector_line(lines, str(operation["selector"]))
        if index in changed_lines:
            raise CanonicalPatchError("Canonical patch contains duplicate selector targets.")
        actual = _parse_scalar(token)
        expected = operation["current"]
        scalar_text_match = (
            isinstance(expected, str)
            and token.strip() == expected
            and not token.strip().startswith(("'", '"'))
        )
        if actual != expected and not scalar_text_match:
            raise CanonicalPatchError(
                f"Canonical selector {operation['selector']} expected {expected!r}, found {actual!r}."
            )
        lines[index] = prefix + render_scalar(operation["proposed"]) + comment
        changed_lines.add(index)
    rendered = newline.join(lines) + (newline if had_final_newline else "")
    candidate = rendered.encode("utf-8")
    try:
        parsed = yaml.safe_load(candidate)
    except yaml.YAMLError as exc:
        raise CanonicalPatchError(f"Rendered canonical candidate is invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CanonicalPatchError("Rendered canonical candidate is not a YAML mapping.")
    return candidate
