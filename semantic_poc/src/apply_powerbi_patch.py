from __future__ import annotations

import argparse
import codecs
import re
import shutil
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .models import (
    CANONICAL_SOURCE,
    DBT_OUTPUT,
    SNOWFLAKE_ENVIRONMENT,
    SNOWFLAKE_OUTPUT,
    STATUS_METADATA_DRIFT,
    build_snowflake_semantic_view,
    clean_tmdl_identifier,
    clean_tmdl_value,
    compare_semantics,
    load_json,
    load_normalized_dbt_semantics,
    load_yaml,
    normalize_expression,
    normalize_text,
    parse_tmdl_definition,
    relative_posix,
    render_compatibility_markdown,
    write_json,
)


ALLOWED_OPERATIONS = {
    "set_measure_expression",
    "set_measure_description",
    "set_measure_format",
    "set_measure_display_folder",
    "set_table_description",
    "set_column_description",
    "set_column_hidden",
}

MEASURE_PROPERTY_OPERATIONS = {
    "set_measure_format": ("formatString", "formatString"),
    "set_measure_display_folder": ("displayFolder", "displayFolder"),
}

PATCHED_POWERBI_OUTPUT = "patched_powerbi_semantics.json"
PATCHED_COMPATIBILITY_OUTPUT = "patched_semantic_compatibility.md"

FORBIDDEN_OPERATION_FIELDS = {
    "dax",
    "expression",
    "lineage",
    "lineage_tag",
    "lineagetag",
    "relationship",
    "relationships",
    "partition",
    "partitions",
    "power_query",
    "powerquery",
    "role",
    "rls",
}


@dataclass(frozen=True)
class TmdlBlock:
    kind: str
    name: str
    header: int
    start: int
    end: int


@dataclass
class TmdlDocument:
    path: Path
    encoding: str
    newline: str
    lines: list[str]
    had_final_newline: bool
    changed: bool = False

    @classmethod
    def load(cls, path: Path) -> "TmdlDocument":
        data = path.read_bytes()
        encoding = "utf-8-sig" if data.startswith(codecs.BOM_UTF8) else "utf-8"
        text = data.decode(encoding)
        newline = "\r\n" if "\r\n" in text else "\n"
        return cls(
            path=path,
            encoding=encoding,
            newline=newline,
            lines=text.splitlines(),
            had_final_newline=text.endswith(("\n", "\r\n")),
        )

    def render(self) -> str:
        text = self.newline.join(self.lines)
        if self.had_final_newline:
            text += self.newline
        return text

    def write_if_changed(self) -> None:
        if self.changed:
            self.path.write_bytes(self.render().encode(self.encoding))

    def block_text(self, block: TmdlBlock) -> str:
        return self.newline.join(self.lines[block.header : block.end])

    def blocks(self) -> dict[str, dict[str, list[TmdlBlock]]]:
        headers: list[tuple[int, str, str]] = []
        for index, line in enumerate(self.lines):
            parsed = parse_header(line)
            if parsed:
                kind, name = parsed
                headers.append((index, kind, name))

        blocks: dict[str, dict[str, list[TmdlBlock]]] = {
            "table": {},
            "measure": {},
            "column": {},
            "partition": {},
        }
        for position, (header, kind, name) in enumerate(headers):
            next_header = headers[position + 1][0] if position + 1 < len(headers) else len(self.lines)
            start = description_start(self.lines, header)
            end = description_start(self.lines, next_header) if next_header < len(self.lines) else len(self.lines)
            blocks[kind].setdefault(name, []).append(TmdlBlock(kind, name, header, start, end))
        return blocks

    def unique_block(self, kind: str, name: str) -> tuple[TmdlBlock | None, str | None]:
        matches = self.blocks().get(kind, {}).get(name, [])
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, f"{kind} `{name}` does not exist exactly once"
        return None, f"{kind} `{name}` is ambiguous ({len(matches)} matches)"


@dataclass
class DefinitionDocuments:
    definition_dir: Path
    tables: dict[str, list[TmdlDocument]]

    @classmethod
    def load(cls, definition_dir: Path) -> "DefinitionDocuments":
        tables: dict[str, list[TmdlDocument]] = {}
        for path in sorted((definition_dir / "tables").glob("*.tmdl")):
            document = TmdlDocument.load(path)
            table_blocks = document.blocks()["table"]
            if not table_blocks:
                tables.setdefault(path.stem, []).append(document)
            for table_name, matches in table_blocks.items():
                tables.setdefault(table_name, []).extend(document for _match in matches)
        return cls(definition_dir=definition_dir, tables=tables)

    def table_document(self, table_name: str) -> tuple[TmdlDocument | None, str | None]:
        matches = self.tables.get(table_name, [])
        if len(matches) == 1:
            document = matches[0]
            table_matches = document.blocks()["table"].get(table_name, [])
            if len(table_matches) == 1:
                return document, None
            if not table_matches:
                return None, f"table `{table_name}` does not exist exactly once"
            return None, f"table `{table_name}` is ambiguous ({len(table_matches)} matches)"
        if not matches:
            return None, f"table `{table_name}` does not exist exactly once"
        return None, f"table `{table_name}` is ambiguous ({len(matches)} matches)"

    def write_changed(self) -> None:
        for documents in self.tables.values():
            for document in documents:
                document.write_if_changed()


@dataclass(frozen=True)
class PreparedOperation:
    operation: dict[str, Any]
    label: str


@dataclass
class PatchApplyResult:
    success: bool
    output_dir: Path
    report_path: Path
    semantics_path: Path
    compatibility_path: Path
    applied: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    protected_checks: list[str] = field(default_factory=list)
    compatibility_before: dict[str, int] | None = None
    compatibility_after: dict[str, int] | None = None


@dataclass(frozen=True)
class ProtectedSnapshot:
    semantics: dict[str, Any]
    relationships: bytes
    partitions: dict[tuple[str, str], str]
    source_files: dict[str, bytes]


def parse_header(line: str) -> tuple[str, str] | None:
    table_match = re.match(r"^table\s+(.+)$", line)
    if table_match:
        return "table", clean_tmdl_identifier(table_match.group(1))
    measure_match = re.match(r"^\tmeasure\s+(.+?)\s*=", line)
    if measure_match:
        return "measure", clean_tmdl_identifier(measure_match.group(1))
    column_match = re.match(r"^\tcolumn\s+(.+)$", line)
    if column_match:
        return "column", clean_tmdl_identifier(column_match.group(1))
    partition_match = re.match(r"^\tpartition\s+(.+?)\s*=", line)
    if partition_match:
        return "partition", clean_tmdl_identifier(partition_match.group(1))
    return None


def description_start(lines: list[str], header: int) -> int:
    start = header
    while start > 0 and lines[start - 1].strip().startswith("///"):
        start -= 1
    return start


def read_description(document: TmdlDocument, block: TmdlBlock) -> str:
    comments = []
    for line in document.lines[block.start : block.header]:
        stripped = line.strip()
        if stripped.startswith("///"):
            comments.append(stripped[3:].strip())
    return normalize_text(" ".join(comments))


def comment_lines(description: str, indent: str) -> list[str]:
    wrapped = textwrap.wrap(description, width=max(40, 96 - len(indent) - 4)) or [description]
    return [f"{indent}/// {line}" for line in wrapped]


def set_description(document: TmdlDocument, block: TmdlBlock, description: str) -> bool:
    indent = re.match(r"^(\s*)", document.lines[block.header]).group(1)
    replacement = comment_lines(description, indent)
    document.lines[block.start : block.header] = replacement
    document.changed = True
    return True


def measure_property(
    document: TmdlDocument,
    block: TmdlBlock,
    property_name: str,
) -> tuple[str | None, str | None]:
    prefix = f"{property_name}:"
    values: list[str] = []
    for line in document.lines[block.header + 1 : block.end]:
        stripped = line.strip()
        if stripped.startswith(prefix):
            values.append(clean_tmdl_value(stripped.split(":", 1)[1]))
    if len(values) > 1:
        return None, f"property `{property_name}` is ambiguous ({len(values)} matches)"
    return (values[0] if values else None), None


def set_measure_property(document: TmdlDocument, block: TmdlBlock, property_name: str, value: str) -> bool:
    prefix = f"{property_name}:"
    property_indent = "\t\t"
    lineage_index: int | None = None
    first_annotation_index: int | None = None
    for index in range(block.header + 1, block.end):
        stripped = document.lines[index].strip()
        if stripped.startswith(prefix):
            indent = re.match(r"^(\s*)", document.lines[index]).group(1)
            document.lines[index] = f"{indent}{property_name}: {value}"
            document.changed = True
            return True
        if stripped.startswith("lineageTag:") and lineage_index is None:
            lineage_index = index
            property_indent = re.match(r"^(\s*)", document.lines[index]).group(1)
        if stripped.startswith("annotation ") and first_annotation_index is None:
            first_annotation_index = index

    insert_index = lineage_index if lineage_index is not None else first_annotation_index
    if insert_index is None:
        return False
    document.lines.insert(insert_index, f"{property_indent}{property_name}: {value}")
    document.changed = True
    return True


def measure_expression(document: TmdlDocument, block: TmdlBlock) -> tuple[str | None, str | None]:
    header = document.lines[block.header]
    if "=" not in header:
        return None, "measure header has no expression delimiter"
    expression = header.split("=", 1)[1].strip()
    if not expression:
        return None, "multiline measure expressions are outside the supported definition-apply scope"
    return expression, None


def set_measure_expression(document: TmdlDocument, block: TmdlBlock, value: str) -> bool:
    header = document.lines[block.header]
    if "=" not in header or not header.split("=", 1)[1].strip():
        return False
    prefix = header.split("=", 1)[0].rstrip()
    document.lines[block.header] = f"{prefix} = {value}"
    document.changed = True
    return True


def column_hidden(document: TmdlDocument, block: TmdlBlock) -> tuple[bool, str | None]:
    matches = sum(
        1 for line in document.lines[block.header + 1 : block.end] if line.strip() == "isHidden"
    )
    if matches > 1:
        return False, f"property `isHidden` is ambiguous ({matches} matches)"
    return matches == 1, None


def set_column_hidden(document: TmdlDocument, block: TmdlBlock, value: bool) -> bool:
    for index in range(block.header + 1, block.end):
        if document.lines[index].strip() == "isHidden":
            if value:
                return True
            del document.lines[index]
            document.changed = True
            return True
    if not value:
        return True

    data_type_index: int | None = None
    insert_indent = "\t\t"
    for index in range(block.header + 1, block.end):
        stripped = document.lines[index].strip()
        if stripped.startswith("dataType:"):
            data_type_index = index + 1
            insert_indent = re.match(r"^(\s*)", document.lines[index]).group(1)
            break
    if data_type_index is None:
        return False
    document.lines.insert(data_type_index, f"{insert_indent}isHidden")
    document.changed = True
    return True


def operation_label(operation: dict[str, Any]) -> str:
    operation_name = operation.get("operation")
    proposed = operation.get("proposed")
    if operation_name == "set_measure_format":
        return f"{operation.get('measure')}: formatString set to {proposed}"
    if operation_name == "set_measure_display_folder":
        return f"{operation.get('measure')}: displayFolder set to {proposed}"
    if operation_name == "set_measure_description":
        return f"{operation.get('measure')}: description set to {proposed}"
    if operation_name == "set_measure_expression":
        return f"{operation.get('measure')}: supported DAX definition updated"
    if operation_name == "set_table_description":
        return f"{operation.get('table')}: description set to {proposed}"
    if operation_name == "set_column_description":
        return f"{operation.get('table')}.{operation.get('column')}: description set to {proposed}"
    if operation_name == "set_column_hidden":
        return f"{operation.get('table')}.{operation.get('column')}: isHidden set to {proposed}"
    return str(operation_name or "unknown operation")


def skipped_operation(item: str, reason: str) -> dict[str, str]:
    return {"item": item, "reason": reason}


def current_matches(operation_name: str, actual: Any, expected: Any) -> bool:
    if operation_name in {"set_measure_description", "set_table_description", "set_column_description"}:
        return normalize_text(str(actual or "")) == normalize_text(str(expected or ""))
    return actual == expected


def already_applied(operation_name: str, actual: Any, proposed: Any) -> bool:
    if operation_name in {"set_measure_description", "set_table_description", "set_column_description"}:
        return normalize_text(str(actual or "")) == normalize_text(str(proposed or ""))
    return actual == proposed


def operation_target_key(operation: dict[str, Any]) -> tuple[str, ...]:
    operation_name = str(operation.get("operation"))
    table_name = str(operation.get("table"))
    if operation_name.startswith("set_measure_"):
        return operation_name, table_name, str(operation.get("measure"))
    if operation_name.startswith("set_column_"):
        return operation_name, table_name, str(operation.get("column"))
    return operation_name, table_name


def validate_operation_shape(operation: Any, index: int) -> list[str]:
    prefix = f"operation {index + 1}"
    if not isinstance(operation, dict):
        return [f"{prefix}: patch operation must be an object"]

    failures: list[str] = []
    operation_name = operation.get("operation")
    if not isinstance(operation_name, str) or not operation_name:
        failures.append(f"{prefix}: operation is missing a string `operation`")
        return failures
    if operation_name not in ALLOWED_OPERATIONS:
        failures.append(f"{prefix}: operation `{operation_name}` is outside the safe metadata patch scope")

    forbidden = sorted(
        str(key)
        for key in operation
        if str(key).lower().replace("-", "_") in FORBIDDEN_OPERATION_FIELDS
    )
    if forbidden:
        failures.append(f"{prefix}: forbidden patch fields: {', '.join(forbidden)}")

    if not isinstance(operation.get("table"), str) or not operation.get("table"):
        failures.append(f"{prefix}: operation is missing a string `table`")
    if operation_name.startswith("set_measure_") and (
        not isinstance(operation.get("measure"), str) or not operation.get("measure")
    ):
        failures.append(f"{prefix}: measure operation is missing a string `measure`")
    if operation_name.startswith("set_column_") and (
        not isinstance(operation.get("column"), str) or not operation.get("column")
    ):
        failures.append(f"{prefix}: column operation is missing a string `column`")
    if "proposed" not in operation:
        failures.append(f"{prefix}: operation is missing `proposed`")
    elif operation_name == "set_column_hidden":
        if not isinstance(operation.get("proposed"), bool):
            failures.append(f"{prefix}: `set_column_hidden` requires a boolean `proposed` value")
    elif operation_name in ALLOWED_OPERATIONS and not isinstance(operation.get("proposed"), str):
        failures.append(f"{prefix}: `{operation_name}` requires a string `proposed` value")
    return failures


def resolve_operation_target(
    documents: DefinitionDocuments,
    operation: dict[str, Any],
) -> tuple[TmdlDocument | None, TmdlBlock | None, Any, str | None]:
    operation_name = operation.get("operation")
    table_name = operation.get("table")
    if not table_name:
        return None, None, None, "operation is missing `table`"
    document, error = documents.table_document(str(table_name))
    if error or document is None:
        return None, None, None, error

    if operation_name == "set_table_description":
        block, error = document.unique_block("table", str(table_name))
        if error or block is None:
            return document, None, None, error
        return document, block, read_description(document, block), None

    if operation_name in MEASURE_PROPERTY_OPERATIONS or operation_name in {
        "set_measure_description",
        "set_measure_expression",
    }:
        measure_name = operation.get("measure")
        if not measure_name:
            return document, None, None, "measure operation is missing `measure`"
        block, error = document.unique_block("measure", str(measure_name))
        if error or block is None:
            return document, None, None, error
        if operation_name == "set_measure_description":
            return document, block, read_description(document, block), None
        if operation_name == "set_measure_expression":
            actual, expression_error = measure_expression(document, block)
            return document, block, actual, expression_error
        property_name, _ = MEASURE_PROPERTY_OPERATIONS[str(operation_name)]
        actual, property_error = measure_property(document, block, property_name)
        return document, block, actual, property_error

    if operation_name in {"set_column_description", "set_column_hidden"}:
        column_name = operation.get("column")
        if not column_name:
            return document, None, None, "column operation is missing `column`"
        block, error = document.unique_block("column", str(column_name))
        if error or block is None:
            return document, None, None, error
        if operation_name == "set_column_description":
            return document, block, read_description(document, block), None
        actual, property_error = column_hidden(document, block)
        return document, block, actual, property_error

    return document, None, None, f"operation `{operation_name}` is outside the safe patch scope"


def validate_operations(
    documents: DefinitionDocuments,
    patch: Any,
    result: PatchApplyResult,
    *,
    allow_metadata: bool = False,
    allow_supported_definitions: bool = False,
) -> list[PreparedOperation]:
    prepared: list[PreparedOperation] = []
    if not isinstance(patch, dict):
        result.failures.append("patch document must be a JSON object")
        return prepared

    legacy = "operations" in patch
    if legacy:
        operations = patch.get("operations")
        skipped_items = patch.get("skipped", [])
    else:
        metadata_operations = patch.get("metadata_operations")
        definition_operations = patch.get("definition_operations")
        if not isinstance(metadata_operations, list) or not isinstance(definition_operations, list):
            result.failures.append(
                "patch document must contain `metadata_operations` and `definition_operations` lists"
            )
            return prepared
        if metadata_operations and not allow_metadata:
            result.failures.append("metadata operations require explicit --allow-metadata")
        if definition_operations and not allow_supported_definitions:
            result.failures.append(
                "definition operations require explicit --allow-supported-definitions"
            )
        operations = metadata_operations + definition_operations
        skipped_items = []
    if not isinstance(operations, list):
        result.failures.append("patch document must contain an `operations` list")
        return prepared
    if not isinstance(skipped_items, list):
        result.failures.append("patch document `skipped` value must be a list")
        return prepared

    for skipped in skipped_items:
        if not isinstance(skipped, dict):
            result.failures.append("each `skipped` patch item must be an object")
            continue
        item = str(skipped.get("item") or "skipped patch item")
        reason = str(skipped.get("reason") or "outside the safe patch scope")
        result.skipped.append(skipped_operation(item, reason))

    seen_targets: set[tuple[str, ...]] = set()
    for index, operation in enumerate(operations):
        shape_failures = validate_operation_shape(operation, index)
        result.failures.extend(shape_failures)
        if shape_failures or not isinstance(operation, dict):
            continue

        operation_name = operation.get("operation")
        label = operation_label(operation)
        target_key = operation_target_key(operation)
        if target_key in seen_targets:
            result.failures.append(f"{label}: duplicate patch target")
            continue
        seen_targets.add(target_key)

        _document, _block, actual, error = resolve_operation_target(documents, operation)
        if error:
            result.failures.append(f"{label}: {error}")
            continue

        proposed = operation.get("proposed")
        if already_applied(str(operation_name), actual, proposed):
            result.skipped.append(skipped_operation(label, "already set"))
            continue
        if "current" in operation and not current_matches(str(operation_name), actual, operation.get("current")):
            result.failures.append(
                f"{label}: expected current value {operation.get('current')!r}, found {actual!r}"
            )
            continue
        prepared.append(PreparedOperation(operation=operation, label=label))
    return prepared


def apply_prepared_operations(
    documents: DefinitionDocuments,
    prepared: list[PreparedOperation],
    result: PatchApplyResult,
) -> None:
    for item in prepared:
        operation = item.operation
        operation_name = str(operation.get("operation"))
        document, block, actual, error = resolve_operation_target(documents, operation)
        if error or document is None or block is None:
            result.failures.append(f"{item.label}: {error or 'target disappeared after copy'}")
            continue
        proposed = operation.get("proposed")
        if already_applied(operation_name, actual, proposed):
            result.skipped.append(skipped_operation(item.label, "already set"))
            continue

        changed = False
        if operation_name == "set_measure_description":
            changed = set_description(document, block, str(proposed))
        elif operation_name == "set_measure_expression":
            changed = set_measure_expression(document, block, str(proposed))
        elif operation_name == "set_table_description":
            changed = set_description(document, block, str(proposed))
        elif operation_name == "set_column_description":
            changed = set_description(document, block, str(proposed))
        elif operation_name in MEASURE_PROPERTY_OPERATIONS:
            property_name, _ = MEASURE_PROPERTY_OPERATIONS[operation_name]
            changed = set_measure_property(document, block, property_name, str(proposed))
        elif operation_name == "set_column_hidden":
            changed = set_column_hidden(document, block, bool(proposed))

        if changed:
            result.applied.append(item.label)
        else:
            result.skipped.append(skipped_operation(item.label, "target block could not be modified safely"))
    documents.write_changed()


def relationship_bytes(definition_dir: Path) -> bytes:
    path = definition_dir / "relationships.tmdl"
    return path.read_bytes() if path.exists() else b""


def partition_blocks(definition_dir: Path) -> dict[tuple[str, str], str]:
    documents = DefinitionDocuments.load(definition_dir)
    blocks: dict[tuple[str, str], str] = {}
    for table_name, table_docs in documents.tables.items():
        for document in table_docs:
            for partition_name, matches in document.blocks()["partition"].items():
                for index, block in enumerate(matches):
                    key = (table_name, partition_name if len(matches) == 1 else f"{partition_name}#{index}")
                    blocks[key] = document.block_text(block)
    return blocks


def definition_files(definition_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(definition_dir).as_posix(): path.read_bytes()
        for path in sorted(definition_dir.rglob("*"))
        if path.is_file()
    }


def lineage_snapshot(semantics: dict[str, Any]) -> dict[tuple[str, ...], Any]:
    snapshot: dict[tuple[str, ...], Any] = {}
    for table_name, table in semantics.get("tables", {}).items():
        snapshot[("table", table_name)] = table.get("lineage_tag")
        for measure_name, measure in table.get("measures", {}).items():
            snapshot[("measure", table_name, measure_name)] = measure.get("lineage_tag")
        for column_name, column in table.get("columns", {}).items():
            snapshot[("column", table_name, column_name)] = column.get("lineage_tag")
    return snapshot


def measure_expression_snapshot(semantics: dict[str, Any]) -> dict[tuple[str, str], str]:
    snapshot: dict[tuple[str, str], str] = {}
    for table_name, table in semantics.get("tables", {}).items():
        for measure_name, measure in table.get("measures", {}).items():
            snapshot[(table_name, measure_name)] = normalize_expression(measure.get("expression"))
    return snapshot


def count_snapshot(semantics: dict[str, Any]) -> dict[str, Any]:
    tables = semantics.get("tables", {})
    return {
        "tables": set(tables),
        "columns": {table: set(data.get("columns", {})) for table, data in tables.items()},
        "measures": {table: set(data.get("measures", {})) for table, data in tables.items()},
    }


def source_column_snapshot(semantics: dict[str, Any]) -> dict[tuple[str, str], Any]:
    return {
        (table_name, column_name): column.get("source_column")
        for table_name, table in semantics.get("tables", {}).items()
        for column_name, column in table.get("columns", {}).items()
    }


def column_type_snapshot(semantics: dict[str, Any]) -> dict[tuple[str, str], tuple[Any, Any]]:
    return {
        (table_name, column_name): (column.get("data_type"), column.get("format_string"))
        for table_name, table in semantics.get("tables", {}).items()
        for column_name, column in table.get("columns", {}).items()
    }


def metadata_snapshot(semantics: dict[str, Any]) -> dict[tuple[str, ...], Any]:
    snapshot: dict[tuple[str, ...], Any] = {}
    for table_name, table in semantics.get("tables", {}).items():
        snapshot[("table_description", table_name)] = normalize_text(table.get("description"))
        for measure_name, measure in table.get("measures", {}).items():
            snapshot[("measure_description", table_name, measure_name)] = normalize_text(
                measure.get("description")
            )
            snapshot[("measure_format", table_name, measure_name)] = measure.get("format_string")
            snapshot[("measure_display_folder", table_name, measure_name)] = measure.get(
                "display_folder"
            )
        for column_name, column in table.get("columns", {}).items():
            snapshot[("column_description", table_name, column_name)] = normalize_text(
                column.get("description")
            )
            snapshot[("column_hidden", table_name, column_name)] = column.get("is_hidden")
    return snapshot


def allowed_metadata_keys(prepared: list[PreparedOperation]) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    prefixes = {
        "set_table_description": "table_description",
        "set_measure_description": "measure_description",
        "set_measure_format": "measure_format",
        "set_measure_display_folder": "measure_display_folder",
        "set_column_description": "column_description",
        "set_column_hidden": "column_hidden",
    }
    for item in prepared:
        operation = item.operation
        operation_name = str(operation["operation"])
        if operation_name == "set_measure_expression":
            continue
        prefix = prefixes[operation_name]
        if operation_name == "set_table_description":
            keys.add((prefix, str(operation["table"])))
        elif operation_name.startswith("set_measure_"):
            keys.add((prefix, str(operation["table"]), str(operation["measure"])))
        else:
            keys.add((prefix, str(operation["table"]), str(operation["column"])))
    return keys


def allowed_definition_keys(prepared: list[PreparedOperation]) -> set[tuple[str, str]]:
    return {
        (str(item.operation["table"]), str(item.operation["measure"]))
        for item in prepared
        if item.operation.get("operation") == "set_measure_expression"
    }


def capture_protected_snapshot(definition_dir: Path) -> ProtectedSnapshot:
    return ProtectedSnapshot(
        semantics=parse_tmdl_definition(definition_dir),
        relationships=relationship_bytes(definition_dir),
        partitions=partition_blocks(definition_dir),
        source_files=definition_files(definition_dir),
    )


def record_check(result: PatchApplyResult, label: str, passed: bool, failure: str) -> None:
    if passed:
        result.protected_checks.append(label)
    else:
        result.failures.append(f"protected check failed: {failure}")


def validate_protected_properties(
    source: ProtectedSnapshot,
    source_dir: Path,
    target_dir: Path,
    prepared: list[PreparedOperation],
    result: PatchApplyResult,
) -> dict[str, Any]:
    target = parse_tmdl_definition(target_dir)

    source_counts = count_snapshot(source.semantics)
    target_counts = count_snapshot(target)
    names_and_counts_match = source_counts == target_counts
    record_check(
        result,
        "table, column, and measure names and counts unchanged",
        names_and_counts_match,
        "table, column, or measure names/counts changed",
    )
    source_expressions = measure_expression_snapshot(source.semantics)
    target_expressions = measure_expression_snapshot(target)
    changed_expressions = {
        key
        for key in source_expressions.keys() | target_expressions.keys()
        if source_expressions.get(key) != target_expressions.get(key)
    }
    unexpected_expressions = changed_expressions - allowed_definition_keys(prepared)
    record_check(
        result,
        "only approved DAX expressions changed",
        not unexpected_expressions,
        "unapproved DAX expressions changed: "
        + ", ".join(".".join(key) for key in sorted(unexpected_expressions)),
    )
    record_check(
        result,
        "lineage tags unchanged",
        lineage_snapshot(source.semantics) == lineage_snapshot(target),
        "lineage tags changed",
    )
    record_check(
        result,
        "relationships unchanged",
        source.relationships == relationship_bytes(target_dir),
        "relationship definitions changed",
    )
    record_check(
        result,
        "partitions and Power Query expressions unchanged",
        source.partitions == partition_blocks(target_dir),
        "partition or Power Query definitions changed",
    )
    record_check(
        result,
        "source-column mappings unchanged",
        source_column_snapshot(source.semantics) == source_column_snapshot(target),
        "source-column mappings changed",
    )
    record_check(
        result,
        "column data types and formats unchanged",
        column_type_snapshot(source.semantics) == column_type_snapshot(target),
        "column data types or formats changed",
    )

    source_metadata = metadata_snapshot(source.semantics)
    target_metadata = metadata_snapshot(target)
    changed_metadata = {
        key
        for key in source_metadata.keys() | target_metadata.keys()
        if source_metadata.get(key) != target_metadata.get(key)
    }
    unexpected_metadata = changed_metadata - allowed_metadata_keys(prepared)
    record_check(
        result,
        "only approved metadata fields changed",
        not unexpected_metadata,
        "unapproved metadata fields changed: "
        + ", ".join(".".join(key) for key in sorted(unexpected_metadata)),
    )

    target_files = definition_files(target_dir)
    record_check(
        result,
        "complete definition file set preserved",
        set(source.source_files) == set(target_files),
        "copied definition file set changed",
    )
    record_check(
        result,
        "source definition folder unchanged",
        source.source_files == definition_files(source_dir),
        "source definition folder changed",
    )
    return target


def render_report(result: PatchApplyResult) -> str:
    lines = ["# Power BI metadata patch result", ""]
    lines.extend(["Applied:"])
    if result.applied:
        lines.extend(f"- {item}" for item in result.applied)
    else:
        lines.append("- None.")

    lines.extend(["", "Skipped:"])
    if result.skipped:
        for item in result.skipped:
            lines.append(f"- {item['item']}")
            lines.append(f"  Reason: {item['reason']}")
    else:
        lines.append("- None.")

    if result.failures:
        lines.extend(["", "Failed:"])
        lines.extend(f"- {failure}" for failure in result.failures)

    lines.extend(["", "Preservation checks:"])
    if result.protected_checks:
        for check in result.protected_checks:
            lines.append(f"- {check}")
    else:
        lines.append("- Not run.")

    if result.compatibility_before is not None:
        lines.extend(
            [
                "",
                "Compatibility before:",
                f"- metadata drift: {result.compatibility_before['metadata_drift']}",
                f"- structural drift: {result.compatibility_before['structural_drift']}",
            ]
        )
    if result.compatibility_after is not None:
        lines.extend(
            [
                "",
                "Compatibility after:",
                f"- metadata drift: {result.compatibility_after['metadata_drift']}",
                f"- structural drift: {result.compatibility_after['structural_drift']}",
            ]
        )
    lines.append("")
    lines.append(f"Output: `{relative_posix(result.output_dir)}`")
    lines.append(f"Patched extraction: `{relative_posix(result.semantics_path)}`")
    lines.append(f"Patched compatibility: `{relative_posix(result.compatibility_path)}`")
    lines.append(f"Status: {'success' if result.success else 'failed'}")
    lines.append("")
    return "\n".join(lines)


def write_report(result: PatchApplyResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(render_report(result), encoding="utf-8", newline="\n")


def paths_overlap(first: Path, second: Path) -> bool:
    first = first.resolve()
    second = second.resolve()
    return first == second or first in second.parents or second in first.parents


def path_is_within(path: Path, parent: Path) -> bool:
    path = path.resolve()
    parent = parent.resolve()
    return path == parent or parent in path.parents


def prepare_output_dir(definition_dir: Path, output_dir: Path) -> tuple[Path, str | None]:
    if paths_overlap(definition_dir, output_dir):
        return output_dir, "--output-dir must not equal, contain, or be contained by --definition-dir"
    if output_dir.exists():
        return output_dir, f"output directory already exists: {output_dir}"
    return output_dir, None


def load_compatibility_inputs(
    patch_path: Path,
    dbt_semantics: dict[str, Any] | None,
    snowflake_view: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_dir = patch_path.parent
    if dbt_semantics is None:
        dbt_candidate = artifact_dir / DBT_OUTPUT.name
        if dbt_candidate.is_file():
            dbt_semantics = load_json(dbt_candidate)
        elif DBT_OUTPUT.is_file():
            dbt_semantics = load_json(DBT_OUTPUT)
        else:
            dbt_semantics = load_normalized_dbt_semantics()
    if snowflake_view is None:
        snowflake_candidate = artifact_dir / SNOWFLAKE_OUTPUT.name
        if snowflake_candidate.is_file():
            snowflake_view = load_yaml(snowflake_candidate)
        elif SNOWFLAKE_OUTPUT.is_file():
            snowflake_view = load_yaml(SNOWFLAKE_OUTPUT)
        else:
            snowflake_view = build_snowflake_semantic_view(
                dbt_semantics,
                load_yaml(SNOWFLAKE_ENVIRONMENT),
            )
    return dbt_semantics, snowflake_view


def compatibility_counts(comparison: dict[str, Any]) -> dict[str, int]:
    statuses = Counter(row.get("status") for row in comparison.get("rows", []))
    return {
        "metadata_drift": statuses[STATUS_METADATA_DRIFT],
        "structural_drift": len(comparison.get("findings", {}).get("relationship_drift", [])),
    }


def write_patched_artifacts(
    result: PatchApplyResult,
    patched_semantics: dict[str, Any],
    comparison: dict[str, Any],
    dbt_semantics: dict[str, Any],
) -> None:
    canonical_source = dbt_semantics.get("canonical_source", CANONICAL_SOURCE)
    compiled_source = dbt_semantics.get("compiled_source", "target/semantic_manifest.json")
    write_json(
        result.semantics_path,
        patched_semantics,
        generated=True,
        canonical_source=canonical_source,
    )
    result.compatibility_path.parent.mkdir(parents=True, exist_ok=True)
    result.compatibility_path.write_text(
        render_compatibility_markdown(comparison, canonical_source, compiled_source),
        encoding="utf-8",
        newline="\n",
    )


def apply_powerbi_patch(
    *,
    definition_dir: Path,
    patch_path: Path,
    output_dir: Path,
    dbt_semantics: dict[str, Any] | None = None,
    snowflake_view: dict[str, Any] | None = None,
    allow_metadata: bool = False,
    allow_supported_definitions: bool = False,
) -> PatchApplyResult:
    definition_dir = definition_dir.resolve()
    patch_path = patch_path.resolve()
    output_dir = output_dir.resolve()
    artifact_dir = patch_path.parent
    target_dir, output_error = prepare_output_dir(definition_dir, output_dir)
    result = PatchApplyResult(
        success=False,
        output_dir=target_dir,
        report_path=artifact_dir / "powerbi_patch_result.md",
        semantics_path=artifact_dir / PATCHED_POWERBI_OUTPUT,
        compatibility_path=artifact_dir / PATCHED_COMPATIBILITY_OUTPUT,
    )

    if path_is_within(artifact_dir, definition_dir):
        result.failures.append("patch and result artifacts must be outside --definition-dir")
        return result
    if output_error:
        result.failures.append(output_error)
        write_report(result)
        return result
    if not definition_dir.is_dir() or not (definition_dir / "tables").is_dir():
        result.failures.append(f"definition folder is missing or incomplete: {definition_dir}")
        write_report(result)
        return result
    if not patch_path.is_file():
        result.failures.append(f"patch file is missing: {patch_path}")
        write_report(result)
        return result

    try:
        patch = load_json(patch_path)
        source_documents = DefinitionDocuments.load(definition_dir)
        if not source_documents.tables:
            raise ValueError("definition folder contains no TMDL table definitions")
        protected_snapshot = capture_protected_snapshot(definition_dir)
        dbt_semantics, snowflake_view = load_compatibility_inputs(
            patch_path,
            dbt_semantics,
            snowflake_view,
        )
        before_comparison = compare_semantics(
            dbt_semantics,
            protected_snapshot.semantics,
            snowflake_view,
        )
        result.compatibility_before = compatibility_counts(before_comparison)
    except Exception as exc:
        result.failures.append(f"input validation failed: {exc}")
        write_report(result)
        return result

    prepared = validate_operations(
        source_documents,
        patch,
        result,
        allow_metadata=allow_metadata,
        allow_supported_definitions=allow_supported_definitions,
    )
    if result.failures:
        write_report(result)
        return result

    copied = False
    try:
        shutil.copytree(definition_dir, target_dir)
        copied = True
        target_documents = DefinitionDocuments.load(target_dir)
        apply_prepared_operations(target_documents, prepared, result)
        if result.failures:
            raise RuntimeError("patch application failed")
        patched_semantics = validate_protected_properties(
            protected_snapshot,
            definition_dir,
            target_dir,
            prepared,
            result,
        )
        if result.failures:
            raise RuntimeError("protected property validation failed")
        after_comparison = compare_semantics(dbt_semantics, patched_semantics, snowflake_view)
        result.compatibility_after = compatibility_counts(after_comparison)
        write_patched_artifacts(
            result,
            patched_semantics,
            after_comparison,
            dbt_semantics,
        )
        result.success = True
    except Exception as exc:  # pragma: no cover - defensive cleanup around file-system operations.
        if not result.failures:
            result.failures.append(str(exc))
        if copied and target_dir.exists():
            shutil.rmtree(target_dir)
    finally:
        write_report(result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a safe Power BI TMDL metadata patch to a copied definition folder.")
    parser.add_argument("--definition-dir", required=True, help="Source Power BI SemanticModel definition folder.")
    parser.add_argument("--patch", required=True, help="Patch JSON generated by the semantic POC.")
    parser.add_argument("--output-dir", required=True, help="Fresh copied output definition folder.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    definition_dir = Path(args.definition_dir)
    result = apply_powerbi_patch(
        definition_dir=definition_dir,
        patch_path=Path(args.patch),
        output_dir=Path(args.output_dir),
    )
    print(f"Power BI patch {'applied' if result.success else 'failed'}")
    print(f"Report: {relative_posix(result.report_path)}")
    print(f"Output: {relative_posix(result.output_dir)}")
    if result.failures:
        print("Failures:", file=sys.stderr)
        for failure in result.failures:
            print(f"- {failure}", file=sys.stderr)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
