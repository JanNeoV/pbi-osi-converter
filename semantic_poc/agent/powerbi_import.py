"""Deterministic, read-only Power BI PBIP/TMDL brownfield inspection.

This module deliberately does not reuse or alter the compatibility extractor in
``semantic_poc.src.models``.  The compatibility extractor is a protected target
representation; brownfield inventory needs substantially more source evidence
and must never write to the inspected definition.

Only a small, explicit DAX grammar is considered equivalent to the canonical
IR: row/column counts, direct column aggregates, same-table sum addition,
addition and positive scaling of resolved measure references, constrained
``KEEPFILTERS`` counts, and two-measure ``DIVIDE``. Everything else is
classified with deterministic diagnostics rather than guessed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

from semantic_poc.src.semantic_ir import (
    Aggregation,
    CanonicalSourceLocation,
    FILTER_CONTEXT_BEHAVIOR_META_KEY,
    FILTER_CONTEXT_INTERSECT_EXISTING,
    FilterOperator,
    FilterPredicate,
    MetricPattern,
    PowerBIMapping,
    SemanticMetricIR,
    SnowflakeMapping,
    SupportClassification,
    build_canonical_metric_ir_index,
    build_metric_ir_index,
    generate_dax_definition,
    generate_snowflake_definition,
    render_canonical_filter_expression,
)


INVENTORY_SCHEMA_VERSION = 2
SUPPORTED_INVENTORY_SCHEMA_VERSIONS = frozenset({1, INVENTORY_SCHEMA_VERSION})
MAPPING_SCHEMA_VERSION = 1
EXTRACTION_VERSION = "powerbi-tmdl-inventory-v1"
CANONICAL_SOURCE = "models/semantic/triathlon_semantic.yml"


class PowerBIImportError(RuntimeError):
    """Base error for deterministic Power BI import inspection."""


class PowerBIPathError(PowerBIImportError):
    """Raised when a requested model path is unsafe, ambiguous, or incomplete."""


class MappingValidationError(PowerBIImportError):
    """Raised when an explicit mapping document is invalid or contradictory."""


class ImportSupportClassification(str, Enum):
    SUPPORTED_EXACT = "SUPPORTED_EXACT"
    SUPPORTED_WITH_MAPPING = "SUPPORTED_WITH_MAPPING"
    SUPPORTED_WITH_ASSUMPTIONS = "SUPPORTED_WITH_ASSUMPTIONS"
    TARGET_SPECIFIC = "TARGET_SPECIFIC"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


class SupportConfidence(str, Enum):
    PROVEN = "PROVEN"
    CONDITIONAL = "CONDITIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


class MappingMethod(str, Enum):
    CONFIGURED_CANONICAL = "CONFIGURED_CANONICAL"
    EXPLICIT = "EXPLICIT"
    EXACT_NORMALIZED = "EXACT_NORMALIZED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class RelationshipFindingType(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    MISSING_CANONICAL_RELATIONSHIP = "MISSING_CANONICAL_RELATIONSHIP"
    EXTRA_TARGET_RELATIONSHIP = "EXTRA_TARGET_RELATIONSHIP"
    AMBIGUOUS_FILTER_PATH = "AMBIGUOUS_FILTER_PATH"
    INACTIVE_RELATIONSHIP_DEPENDENCY = "INACTIVE_RELATIONSHIP_DEPENDENCY"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


@dataclass(frozen=True)
class ImportDiagnostic:
    code: str
    message: str
    severity: str = "ERROR"
    source_location: "SourceLocation | None" = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.source_location is not None:
            value["source_location"] = self.source_location.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImportDiagnostic":
        location = value.get("source_location")
        return cls(
            code=str(value["code"]),
            message=str(value["message"]),
            severity=str(value.get("severity", "ERROR")),
            source_location=SourceLocation.from_dict(location) if isinstance(location, Mapping) else None,
        )


@dataclass(frozen=True, order=True)
class SourceLocation:
    """Repository-portable source location relative to the definition root."""

    file: str
    line: int
    column: int = 1
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"file": self.file, "line": self.line, "column": self.column}
        if self.end_line is not None:
            value["end_line"] = self.end_line
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceLocation":
        return cls(
            file=str(value["file"]),
            line=int(value["line"]),
            column=int(value.get("column", 1)),
            end_line=int(value["end_line"]) if value.get("end_line") is not None else None,
        )


@dataclass(frozen=True)
class ObjectSupportRecord:
    object_id: str
    object_kind: str
    object_name: str
    source_location: SourceLocation | None
    classification: ImportSupportClassification
    confidence: SupportConfidence
    classifier_rule_id: str
    dependencies: tuple[str, ...] = ()
    recognized_pattern: str | None = None
    required_mappings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    unsupported_constructs: tuple[str, ...] = ()
    diagnostics: tuple[ImportDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "object_kind": self.object_kind,
            "object_name": self.object_name,
            "source_location": self.source_location.to_dict() if self.source_location else None,
            "classification": self.classification.value,
            "confidence": self.confidence.value,
            "classifier_rule_id": self.classifier_rule_id,
            "dependencies": list(self.dependencies),
            "recognized_pattern": self.recognized_pattern,
            "required_mappings": list(self.required_mappings),
            "assumptions": list(self.assumptions),
            "unsupported_constructs": list(self.unsupported_constructs),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObjectSupportRecord":
        location = value.get("source_location")
        return cls(
            object_id=str(value["object_id"]),
            object_kind=str(value["object_kind"]),
            object_name=str(value["object_name"]),
            source_location=(
                SourceLocation.from_dict(location) if isinstance(location, Mapping) else None
            ),
            classification=ImportSupportClassification(str(value["classification"])),
            confidence=SupportConfidence(str(value["confidence"])),
            classifier_rule_id=str(value["classifier_rule_id"]),
            dependencies=tuple(str(item) for item in value.get("dependencies", [])),
            recognized_pattern=(
                str(value["recognized_pattern"])
                if value.get("recognized_pattern") is not None
                else None
            ),
            required_mappings=tuple(str(item) for item in value.get("required_mappings", [])),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            unsupported_constructs=tuple(
                str(item) for item in value.get("unsupported_constructs", [])
            ),
            diagnostics=tuple(
                ImportDiagnostic.from_dict(item)
                for item in value.get("diagnostics", [])
                if isinstance(item, Mapping)
            ),
        )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _object_id(kind: str, *parts: str) -> str:
    identity = "\x1f".join((kind, *(part.casefold() for part in parts)))
    return f"pbi_{kind}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def _clean_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('""', '"')
    return value


def _split_qualified_tmdl_reference(value: str) -> tuple[str, str]:
    in_quote = False
    separators: list[int] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "'":
            if in_quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif character == "." and not in_quote:
            separators.append(index)
        index += 1
    if in_quote or not separators:
        raise PowerBIImportError(f"Invalid qualified TMDL reference: {value!r}")
    separator = separators[-1]
    table = _clean_identifier(value[:separator].strip())
    column = _clean_identifier(value[separator + 1 :].strip())
    if not table or not column:
        raise PowerBIImportError(f"Invalid qualified TMDL reference: {value!r}")
    return table, column


def _indent_width(line: str) -> int:
    width = 0
    for character in line:
        if character == "\t":
            width += 4
        elif character == " ":
            width += 1
        else:
            break
    return width


def _split_tmdl_assignment(value: str) -> tuple[str, str | None]:
    """Split a TMDL declaration at the first unquoted equals sign."""

    in_quote = False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "'":
            if in_quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif character == "=" and not in_quote:
            return value[:index].strip(), value[index + 1:].strip()
        index += 1
    if in_quote:
        raise PowerBIImportError(f"Unterminated quoted TMDL identifier: {value!r}")
    return value.strip(), None


def _tmdl_declaration(stripped: str, keyword: str) -> tuple[str, str | None] | None:
    prefix = keyword + " "
    if not stripped.startswith(prefix):
        return None
    identifier, assignment = _split_tmdl_assignment(stripped[len(prefix):])
    if not identifier:
        raise PowerBIImportError(f"Empty {keyword} identifier in TMDL declaration.")
    return _clean_identifier(identifier), assignment


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda p: p.as_posix()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        payload = item.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _ensure_contained(path: Path, repository_root: Path, *, label: str) -> Path:
    root = repository_root.resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PowerBIPathError(f"{label} does not exist: {path}") from exc
    if not resolved.is_relative_to(root):
        raise PowerBIPathError(f"{label} must resolve inside repository root {root}.")
    return resolved


def _validate_contained_tree(path: Path, repository_root: Path, *, label: str) -> None:
    """Reject nested links that escape the repository or hide a source subtree."""

    root = repository_root.resolve(strict=True)
    for item in path.rglob("*"):
        try:
            resolved = item.resolve(strict=True)
        except OSError as exc:
            raise PowerBIPathError(f"{label} contains an unreadable path: {item}") from exc
        if not resolved.is_relative_to(root):
            raise PowerBIPathError(f"{label} contains a symlink escape: {item}")
        if item.is_symlink() and resolved.is_dir():
            raise PowerBIPathError(f"{label} contains a symbolic-link directory: {item}")


def _read_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PowerBIPathError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise PowerBIPathError(f"{label} must contain a JSON object: {path}")
    return value


def _definition_from_semantic_model(model_dir: Path, repository_root: Path) -> Path:
    model_dir = _ensure_contained(model_dir, repository_root, label="SemanticModel directory")
    if not model_dir.is_dir() or not model_dir.name.casefold().endswith(".semanticmodel"):
        raise PowerBIPathError(f"Expected a .SemanticModel directory, got: {model_dir}")
    definition = _ensure_contained(model_dir / "definition", repository_root, label="TMDL definition directory")
    return _validate_definition_dir(definition, repository_root)


def _validate_definition_dir(definition: Path, repository_root: Path) -> Path:
    definition = _ensure_contained(definition, repository_root, label="TMDL definition directory")
    if not definition.is_dir() or definition.name.casefold() != "definition":
        raise PowerBIPathError(f"Expected a TMDL definition directory, got: {definition}")
    if not definition.parent.name.casefold().endswith(".semanticmodel"):
        raise PowerBIPathError("A definition directory must belong to a .SemanticModel directory.")
    required = (definition / "database.tmdl", definition / "model.tmdl", definition / "tables")
    missing = [item.name for item in required if not item.exists()]
    if missing or not any((definition / "tables").glob("*.tmdl")):
        detail = ", ".join(missing or ["tables/*.tmdl"])
        raise PowerBIPathError(f"Incomplete TMDL definition; missing {detail}.")
    for item in required:
        _ensure_contained(item, repository_root, label="TMDL component")
    # The public resolver is itself a safety boundary.  Do not defer nested
    # link validation to extraction: callers may use the resolved definition
    # for other read-only operations.
    _validate_contained_tree(definition, repository_root, label="TMDL definition")
    return definition


def resolve_powerbi_model_dir(
    model_path: str | Path,
    repository_root: str | Path | None = None,
) -> Path:
    """Resolve a repository-contained .pbip/.SemanticModel/definition to TMDL.

    Symlinks are resolved before containment checks.  PBIP projects must resolve
    to exactly one semantic model through their report ``definition.pbir``.
    """

    root = Path(repository_root or Path.cwd()).resolve(strict=True)
    requested = Path(model_path)
    if not requested.is_absolute():
        requested = root / requested
    requested = _ensure_contained(requested, root, label="Power BI model path")

    if requested.is_dir() and requested.name.casefold() == "definition":
        return _validate_definition_dir(requested, root)
    if requested.is_dir() and requested.name.casefold().endswith(".semanticmodel"):
        return _definition_from_semantic_model(requested, root)
    if not requested.is_file() or requested.suffix.casefold() != ".pbip":
        raise PowerBIPathError("model_path must be a .pbip file, .SemanticModel directory, or definition directory.")

    project = _read_json(requested, label="PBIP project")
    candidates: set[Path] = set()
    for artifact in project.get("artifacts", []) or []:
        if not isinstance(artifact, Mapping) or not isinstance(artifact.get("report"), Mapping):
            continue
        report_path = artifact["report"].get("path")
        if not isinstance(report_path, str) or not report_path:
            continue
        report_dir = _ensure_contained(requested.parent / report_path, root, label="PBIP report directory")
        pbir_path = _ensure_contained(
            report_dir / "definition.pbir", root, label="PBIR definition"
        )
        pbir = _read_json(pbir_path, label="PBIR definition")
        by_path = ((pbir.get("datasetReference") or {}).get("byPath") or {})
        semantic_path = by_path.get("path") if isinstance(by_path, Mapping) else None
        if isinstance(semantic_path, str) and semantic_path:
            candidates.add(_ensure_contained(report_dir / semantic_path, root, label="PBIP semantic model directory"))

    if not candidates:
        sibling = requested.with_suffix("")
        conventional = sibling.parent / f"{sibling.name}.SemanticModel"
        if conventional.exists():
            candidates.add(_ensure_contained(conventional, root, label="PBIP semantic model directory"))
    if len(candidates) != 1:
        raise PowerBIPathError(f"PBIP project must resolve to exactly one semantic model; found {len(candidates)}.")
    return _definition_from_semantic_model(next(iter(candidates)), root)


@dataclass(frozen=True)
class DaxFilter:
    table: str
    column: str
    value: bool | int | float | str
    keep_filters: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = {"table": self.table, "column": self.column, "operator": "EQ", "value": self.value}
        if self.keep_filters:
            result["keep_filters"] = True
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DaxFilter":
        return cls(
            str(value["table"]),
            str(value["column"]),
            value.get("value"),
            bool(value.get("keep_filters")),
        )


@dataclass(frozen=True)
class DaxAnalysis:
    pattern: str | None
    supported: bool
    ast: Mapping[str, Any] | None
    measure_dependencies: tuple[str, ...] = ()
    table_dependencies: tuple[str, ...] = ()
    column_dependencies: tuple[str, ...] = ()
    filters: tuple[DaxFilter, ...] = ()
    diagnostics: tuple[ImportDiagnostic, ...] = ()
    normalized_expression: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "supported": self.supported,
            "ast": dict(self.ast) if self.ast is not None else None,
            "measure_dependencies": list(self.measure_dependencies),
            "table_dependencies": list(self.table_dependencies),
            "column_dependencies": list(self.column_dependencies),
            "filters": [item.to_dict() for item in self.filters],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "normalized_expression": self.normalized_expression,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DaxAnalysis":
        ast = value.get("ast")
        return cls(
            pattern=str(value["pattern"]) if value.get("pattern") is not None else None,
            supported=bool(value.get("supported")),
            ast=dict(ast) if isinstance(ast, Mapping) else None,
            measure_dependencies=tuple(str(item) for item in value.get("measure_dependencies", [])),
            table_dependencies=tuple(str(item) for item in value.get("table_dependencies", [])),
            column_dependencies=tuple(str(item) for item in value.get("column_dependencies", [])),
            filters=tuple(DaxFilter.from_dict(item) for item in value.get("filters", []) if isinstance(item, Mapping)),
            diagnostics=tuple(
                ImportDiagnostic.from_dict(item) for item in value.get("diagnostics", []) if isinstance(item, Mapping)
            ),
            normalized_expression=str(value.get("normalized_expression", "")),
        )


@dataclass(frozen=True)
class _DaxToken:
    kind: str
    value: str
    offset: int


class _DaxSyntaxError(ValueError):
    pass


def _tokenize_dax(expression: str) -> tuple[_DaxToken, ...]:
    tokens: list[_DaxToken] = []
    i = 0
    while i < len(expression):
        character = expression[i]
        if character.isspace():
            i += 1
            continue
        if expression.startswith("//", i) or expression.startswith("--", i):
            end = expression.find("\n", i + 2)
            i = len(expression) if end < 0 else end + 1
            continue
        if expression.startswith("/*", i):
            end = expression.find("*/", i + 2)
            if end < 0:
                raise _DaxSyntaxError("Unterminated block comment.")
            i = end + 2
            continue
        if character in "(),=/+":
            tokens.append(_DaxToken({"(": "LPAREN", ")": "RPAREN", ",": "COMMA", "=": "EQ", "/": "DIV", "+": "PLUS"}[character], character, i))
            i += 1
            continue
        if character == "'":
            start = i
            i += 1
            value: list[str] = []
            while i < len(expression):
                if expression[i] == "'":
                    if i + 1 < len(expression) and expression[i + 1] == "'":
                        value.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                value.append(expression[i])
                i += 1
            else:
                raise _DaxSyntaxError("Unterminated quoted identifier.")
            tokens.append(_DaxToken("IDENT", "".join(value), start))
            continue
        if character == "[":
            start = i
            i += 1
            value = []
            while i < len(expression):
                if expression[i] == "]":
                    if i + 1 < len(expression) and expression[i + 1] == "]":
                        value.append("]")
                        i += 2
                        continue
                    i += 1
                    break
                value.append(expression[i])
                i += 1
            else:
                raise _DaxSyntaxError("Unterminated bracketed identifier.")
            tokens.append(_DaxToken("BRACKET", "".join(value), start))
            continue
        if character == '"':
            start = i
            i += 1
            value = []
            while i < len(expression):
                if expression[i] == '"':
                    if i + 1 < len(expression) and expression[i + 1] == '"':
                        value.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                value.append(expression[i])
                i += 1
            else:
                raise _DaxSyntaxError("Unterminated string literal.")
            tokens.append(_DaxToken("STRING", "".join(value), start))
            continue
        number = re.match(r"-?(?:\d+(?:\.\d*)?|\.\d+)", expression[i:])
        if number:
            value = number.group(0)
            tokens.append(_DaxToken("NUMBER", value, i))
            i += len(value)
            continue
        identifier = re.match(r"[A-Za-z_][A-Za-z0-9_]*", expression[i:])
        if identifier:
            value = identifier.group(0)
            tokens.append(_DaxToken("IDENT", value, i))
            i += len(value)
            continue
        raise _DaxSyntaxError(f"Unexpected character {character!r} at offset {i}.")
    return tuple(tokens)


def _diagnostic_tokens_from_expression(expression: str) -> tuple[_DaxToken, ...]:
    """Recover function names after lexical failure so diagnostics stay specific."""

    tokens: list[_DaxToken] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression):
        tokens.append(_DaxToken("IDENT", match.group(1), match.start(1)))
        tokens.append(_DaxToken("LPAREN", "(", match.end(1)))
    return tuple(tokens)


class _DaxParser:
    def __init__(self, tokens: Sequence[_DaxToken]) -> None:
        self.tokens = tokens
        self.index = 0

    def current(self) -> _DaxToken | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def accept(self, kind: str, value: str | None = None) -> _DaxToken | None:
        token = self.current()
        if token is None or token.kind != kind or (value is not None and token.value.casefold() != value.casefold()):
            return None
        self.index += 1
        return token

    def require(self, kind: str, value: str | None = None) -> _DaxToken:
        token = self.accept(kind, value)
        if token is None:
            expected = value or kind
            actual = self.current().value if self.current() else "end of expression"
            raise _DaxSyntaxError(f"Expected {expected}, found {actual}.")
        return token

    def function(self, name: str) -> None:
        self.require("IDENT", name)
        self.require("LPAREN")

    def table(self) -> str:
        return self.require("IDENT").value

    def measure_ref(self) -> str:
        # A qualified reference is accepted only in this measure-only grammar;
        # dependency resolution still rejects duplicate model-wide names.
        if (
            self.current() is not None
            and self.current().kind == "IDENT"
            and self.index + 1 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "BRACKET"
        ):
            self.index += 1
        return self.require("BRACKET").value

    def literal(self) -> bool | int | float | str:
        token = self.current()
        if token is None:
            raise _DaxSyntaxError("Expected equality literal.")
        if token.kind == "STRING":
            self.index += 1
            return token.value
        if token.kind == "NUMBER":
            self.index += 1
            return float(token.value) if "." in token.value else int(token.value)
        if token.kind == "IDENT" and token.value.casefold() in {"true", "false"}:
            self.index += 1
            value = token.value.casefold() == "true"
            if self.accept("LPAREN"):
                self.require("RPAREN")
            return value
        raise _DaxSyntaxError("Only Boolean, numeric, and string equality literals are supported.")

    def filter(self) -> DaxFilter:
        table = self.table()
        column = self.require("BRACKET").value
        self.require("EQ")
        return DaxFilter(table, column, self.literal())

    def filter_argument(self) -> DaxFilter:
        if (
            self.current() is not None
            and self.current().kind == "IDENT"
            and self.current().value.casefold() == "keepfilters"
        ):
            self.function("KEEPFILTERS")
            predicate = self.filter()
            self.require("RPAREN")
            if not isinstance(predicate.value, bool):
                raise _DaxSyntaxError(
                    "KEEPFILTERS is supported only for one Boolean equality predicate."
                )
            return DaxFilter(
                predicate.table,
                predicate.column,
                predicate.value,
                keep_filters=True,
            )
        return self.filter()

    def column_ref(self) -> tuple[str, str]:
        table = self.table()
        column = self.require("BRACKET").value
        return table, column

    def aggregate(self, function_name: str) -> tuple[str, str]:
        self.function(function_name)
        table, column = self.column_ref()
        self.require("RPAREN")
        return table, column

    def parse(self) -> tuple[str, dict[str, Any], tuple[DaxFilter, ...], tuple[str, ...]]:
        first = self.current()
        if first is None:
            raise _DaxSyntaxError("DAX expression must not be empty.")
        if first.kind == "BRACKET" or (
            first.kind == "IDENT"
            and self.index + 1 < len(self.tokens)
            and self.tokens[self.index + 1].kind == "BRACKET"
        ):
            references = [self.measure_ref()]
            while self.accept("PLUS"):
                references.append(self.measure_ref())
            if len(references) < 2:
                raise _DaxSyntaxError(
                    "A direct measure reference requires at least one supported addition operator."
                )
            result = (
                "METRIC_ADDITION",
                {"kind": "METRIC_ADDITION", "references": references},
                (),
                tuple(references),
            )
            if self.current() is not None:
                raise _DaxSyntaxError(f"Unexpected trailing token {self.current().value!r}.")
            return result
        if first.kind != "IDENT":
            raise _DaxSyntaxError("DAX expression must start with a supported function.")
        name = first.value.casefold()
        if name == "countrows":
            self.function("COUNTROWS")
            table = self.table()
            self.require("RPAREN")
            result = ("COUNT", {"kind": "COUNT", "table": table}, (), ())
        elif name in {"sum", "average", "min", "max", "count", "distinctcount"}:
            function_name = first.value.upper()
            table, column = self.aggregate(function_name)
            kind = {
                "SUM": "SUM",
                "AVERAGE": "AVERAGE",
                "MIN": "MIN",
                "MAX": "MAX",
                "COUNT": "COLUMN_COUNT",
                "DISTINCTCOUNT": "DISTINCT_COUNT",
            }[function_name]
            ast: dict[str, Any] = {
                "kind": kind,
                "table": table,
                "column": column,
            }
            if self.accept("PLUS"):
                if kind != "SUM":
                    raise _DaxSyntaxError("Only SUM aggregates may use the supported addition form.")
                fields = [{"table": table, "column": column}]
                while True:
                    next_table, next_column = self.aggregate("SUM")
                    fields.append({"table": next_table, "column": next_column})
                    if not self.accept("PLUS"):
                        break
                if any(item["table"].casefold() != table.casefold() for item in fields):
                    raise _DaxSyntaxError("Added SUM aggregates must target the same table.")
                kind = "SUM_ADDITION"
                ast = {"kind": kind, "table": table, "fields": fields}
            elif self.accept("DIV"):
                if kind != "SUM":
                    raise _DaxSyntaxError("Only SUM may use the supported scalar divisor form.")
                divisor = self.require("NUMBER").value
                if float(divisor) <= 0:
                    raise _DaxSyntaxError("Scaled SUM divisor must be positive.")
                ast["kind"] = "SCALED_SUM"
                ast["divisor"] = divisor
                kind = "SCALED_SUM"
            result = (kind, ast, (), ())
        elif name == "calculate":
            self.function("CALCULATE")
            self.function("COUNTROWS")
            table = self.table()
            self.require("RPAREN")
            self.require("COMMA")
            filters = [self.filter_argument()]
            while self.accept("COMMA"):
                filters.append(self.filter_argument())
            self.require("RPAREN")
            if any(item.table.casefold() != table.casefold() for item in filters):
                raise _DaxSyntaxError("Equality filters must target the counted table.")
            if sum(item.keep_filters for item in filters) > 1:
                raise _DaxSyntaxError(
                    "Multiple KEEPFILTERS predicates require manual review."
                )
            if any(item.keep_filters for item in filters) and not all(
                item.keep_filters for item in filters
            ):
                raise _DaxSyntaxError(
                    "Mixed KEEPFILTERS and replacing predicates require manual review."
                )
            ast = {"kind": "FILTERED_COUNT", "table": table, "filters": [item.to_dict() for item in filters]}
            result = ("FILTERED_COUNT", ast, tuple(filters), ())
        elif name == "divide":
            self.function("DIVIDE")
            if self.current() is not None and self.current().kind == "IDENT" and self.current().value.casefold() == "sum":
                table, column = self.aggregate("SUM")
                self.require("COMMA")
                divisor = self.require("NUMBER").value
                if float(divisor) <= 0:
                    raise _DaxSyntaxError("Scaled SUM divisor must be positive.")
                self.require("RPAREN")
                result = (
                    "SCALED_SUM",
                    {
                        "kind": "SCALED_SUM",
                        "table": table,
                        "column": column,
                        "divisor": divisor,
                    },
                    (),
                    (),
                )
            else:
                numerator = self.measure_ref()
                self.require("COMMA")
                if self.current() is not None and self.current().kind == "NUMBER":
                    divisor = self.require("NUMBER").value
                    if float(divisor) <= 0:
                        raise _DaxSyntaxError("Scaled metric divisor must be positive.")
                    self.require("RPAREN")
                    result = (
                        "SCALED_METRIC",
                        {
                            "kind": "SCALED_METRIC",
                            "reference": numerator,
                            "divisor": divisor,
                        },
                        (),
                        (numerator,),
                    )
                else:
                    denominator = self.measure_ref()
                    self.require("RPAREN")
                    result = (
                        "RATIO",
                        {"kind": "RATIO", "numerator": numerator, "denominator": denominator},
                        (),
                        (numerator, denominator),
                    )
        else:
            raise _DaxSyntaxError(f"Function {first.value} is outside the supported DAX grammar.")
        if self.current() is not None:
            raise _DaxSyntaxError(f"Unexpected trailing token {self.current().value!r}.")
        return result


_ITERATOR_FUNCTIONS = frozenset(
    {"SUMX", "AVERAGEX", "COUNTX", "COUNTAX", "MINX", "MAXX", "PRODUCTX", "CONCATENATEX", "RANKX"}
)
_FILTER_MODIFIERS = frozenset({"ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "KEEPFILTERS", "CROSSFILTER"})
_FILTER_STATE_FUNCTIONS = frozenset(
    {"ISCROSSFILTERED", "ISFILTERED", "HASONEFILTER", "HASONEVALUE"}
)
_VISUAL_SCOPE_FUNCTIONS = frozenset({"ISINSCOPE"})
_TIME_INTELLIGENCE = frozenset(
    {
        "DATEADD",
        "DATESBETWEEN",
        "DATESINPERIOD",
        "DATESMTD",
        "DATESQTD",
        "DATESYTD",
        "PARALLELPERIOD",
        "PREVIOUSDAY",
        "PREVIOUSMONTH",
        "PREVIOUSQUARTER",
        "PREVIOUSYEAR",
        "SAMEPERIODLASTYEAR",
        "TOTALMTD",
        "TOTALQTD",
        "TOTALYTD",
    }
)
_VIRTUAL_TABLE_FUNCTIONS = frozenset(
    {"ADDCOLUMNS", "CROSSJOIN", "DISTINCT", "EXCEPT", "FILTER", "GENERATE", "GROUPBY", "INTERSECT", "SELECTCOLUMNS", "SUMMARIZE", "SUMMARIZECOLUMNS", "UNION", "VALUES"}
)

def _function_names(tokens: Sequence[_DaxToken]) -> tuple[str, ...]:
    return tuple(
        token.value.upper()
        for index, token in enumerate(tokens[:-1])
        if token.kind == "IDENT" and tokens[index + 1].kind == "LPAREN"
    )


def _unqualified_bracket_references(tokens: Sequence[_DaxToken]) -> tuple[str, ...]:
    """Return bracketed references that are not qualified table columns."""

    return tuple(
        token.value
        for index, token in enumerate(tokens)
        if token.kind == "BRACKET"
        and not (index > 0 and tokens[index - 1].kind == "IDENT")
    )


def _diagnose_dax(tokens: Sequence[_DaxToken], error: str | None) -> tuple[ImportDiagnostic, ...]:
    functions = _function_names(tokens)
    diagnostics: list[ImportDiagnostic] = []
    if functions.count("CALCULATE") > 1:
        diagnostics.append(ImportDiagnostic("DAX_NESTED_CALCULATE", "Nested CALCULATE requires manual review."))
    iterators = sorted(set(functions) & _ITERATOR_FUNCTIONS)
    if iterators:
        diagnostics.append(ImportDiagnostic("DAX_ITERATOR_UNSUPPORTED", f"DAX iterator(s) are unsupported: {', '.join(iterators)}."))
    modifiers = sorted(set(functions) & _FILTER_MODIFIERS)
    if error is None:
        modifiers = [item for item in modifiers if item != "KEEPFILTERS"]
    if modifiers:
        diagnostics.append(
            ImportDiagnostic("DAX_FILTER_CONTEXT_MODIFIER", f"Filter-context modifier(s) require manual review: {', '.join(modifiers)}.")
        )
    if "USERELATIONSHIP" in functions:
        diagnostics.append(
            ImportDiagnostic("DAX_INACTIVE_RELATIONSHIP_DEPENDENCY", "USERELATIONSHIP depends on inactive relationship behavior.")
        )
    time_functions = sorted(set(functions) & _TIME_INTELLIGENCE)
    if time_functions:
        diagnostics.append(
            ImportDiagnostic("DAX_TIME_INTELLIGENCE", f"Time-intelligence function(s) require manual review: {', '.join(time_functions)}.")
        )
    virtual = sorted(set(functions) & _VIRTUAL_TABLE_FUNCTIONS)
    if virtual:
        diagnostics.append(
            ImportDiagnostic("DAX_VIRTUAL_TABLE", f"Virtual-table construct(s) are unsupported: {', '.join(virtual)}.")
        )
    filter_state = sorted(set(functions) & _FILTER_STATE_FUNCTIONS)
    if filter_state and not diagnostics:
        diagnostics.append(
            ImportDiagnostic(
                "DAX_FILTER_STATE_DEPENDENCY",
                "Filter-state function(s) depend on query filters, including filters "
                f"propagated through relationships, and require manual review: {', '.join(filter_state)}.",
            )
        )
    visual_scope = sorted(set(functions) & _VISUAL_SCOPE_FUNCTIONS)
    if visual_scope and not diagnostics:
        diagnostics.append(
            ImportDiagnostic(
                "DAX_VISUAL_SCOPE_DEPENDENCY",
                "Visual-scope function(s) depend on report grouping context and "
                f"require manual review: {', '.join(visual_scope)}.",
            )
        )
    if error and "CALCULATE" in functions and not any(
        item.code
        in {
            "DAX_NESTED_CALCULATE",
            "DAX_FILTER_CONTEXT_MODIFIER",
            "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY",
            "DAX_FILTER_STATE_DEPENDENCY",
            "DAX_VISUAL_SCOPE_DEPENDENCY",
        }
        for item in diagnostics
    ):
        diagnostics.append(
            ImportDiagnostic(
                "DAX_CONTEXT_TRANSITION",
                "CALCULATE context transition is only supported for COUNTROWS with flat equality filters.",
            )
        )
    if error and not diagnostics:
        first = functions[0] if functions else None
        if first and first not in {
            "COUNTROWS",
            "COUNT",
            "SUM",
            "AVERAGE",
            "MIN",
            "MAX",
            "DISTINCTCOUNT",
            "CALCULATE",
            "DIVIDE",
            "KEEPFILTERS",
            "TRUE",
            "FALSE",
        }:
            diagnostics.append(ImportDiagnostic("DAX_FUNCTION_UNSUPPORTED", f"Function {first} is outside the supported deterministic grammar."))
        else:
            diagnostics.append(ImportDiagnostic("DAX_PATTERN_UNSUPPORTED", error))
    return tuple(diagnostics)


def analyze_dax_measure(
    expression: str,
    *,
    known_measure_names: Iterable[str] = (),
    has_dynamic_format_string: bool = False,
    calculation_group_context: bool = False,
) -> DaxAnalysis:
    """Analyze one measure using the deliberately constrained M5 DAX grammar."""

    if not isinstance(expression, str) or not expression.strip():
        return DaxAnalysis(
            None,
            False,
            None,
            diagnostics=(ImportDiagnostic("DAX_EXPRESSION_MISSING", "Measure has no DAX expression."),),
        )
    try:
        tokens = _tokenize_dax(expression)
    except _DaxSyntaxError as exc:
        recovered = _diagnostic_tokens_from_expression(expression)
        diagnostics = _diagnose_dax(recovered, str(exc))
        if not diagnostics:
            diagnostics = (ImportDiagnostic("DAX_TOKENIZATION_ERROR", str(exc)),)
        return DaxAnalysis(
            None,
            False,
            None,
            diagnostics=diagnostics,
        )

    error: str | None = None
    parsed: tuple[str, dict[str, Any], tuple[DaxFilter, ...], tuple[str, ...]] | None = None
    try:
        parsed = _DaxParser(tokens).parse()
    except _DaxSyntaxError as exc:
        error = str(exc)

    diagnostics = list(_diagnose_dax(tokens, error))
    if has_dynamic_format_string:
        diagnostics.append(
            ImportDiagnostic("DAX_DYNAMIC_FORMAT_STRING", "Dynamic format strings are target-specific and require manual review.")
        )
    if calculation_group_context:
        diagnostics.append(
            ImportDiagnostic("DAX_CALCULATION_GROUP", "Calculation-group behavior is target-specific and requires manual review.")
        )

    known = {name.casefold(): name for name in known_measure_names}
    bracket_values = _unqualified_bracket_references(tokens)
    extracted_dependencies = {
        known[value.casefold()]
        for value in bracket_values
        if value.casefold() in known
    }
    if parsed is None:
        return DaxAnalysis(
            None,
            False,
            None,
            measure_dependencies=tuple(sorted(extracted_dependencies, key=str.casefold)),
            diagnostics=tuple(diagnostics),
            normalized_expression="".join(token.value.casefold() for token in tokens),
        )

    pattern, ast, filters, dependencies = parsed
    extracted_dependencies.update(dependencies)
    tables = (
        {str(ast["table"])}
        if pattern
        in {
            "COUNT",
            "COLUMN_COUNT",
            "SUM",
            "AVERAGE",
            "MIN",
            "MAX",
            "DISTINCT_COUNT",
            "SCALED_SUM",
            "SUM_ADDITION",
            "FILTERED_COUNT",
        }
        else set()
    )
    columns = {f"{item.table}[{item.column}]" for item in filters}
    if pattern in {
        "COLUMN_COUNT",
        "SUM",
        "AVERAGE",
        "MIN",
        "MAX",
        "DISTINCT_COUNT",
        "SCALED_SUM",
    }:
        columns.add(f"{ast['table']}[{ast['column']}]")
    elif pattern == "SUM_ADDITION":
        columns.update(
            f"{item['table']}[{item['column']}]"
            for item in ast["fields"]
        )
    supported = not diagnostics
    return DaxAnalysis(
        pattern,
        supported,
        ast,
        measure_dependencies=tuple(sorted(extracted_dependencies, key=str.casefold)),
        table_dependencies=tuple(sorted(tables, key=str.casefold)),
        column_dependencies=tuple(sorted(columns, key=str.casefold)),
        filters=filters,
        diagnostics=tuple(diagnostics),
        normalized_expression="".join(token.value.casefold() for token in tokens),
    )


@dataclass(frozen=True)
class PowerBIColumn:
    object_id: str
    table: str
    name: str
    data_type: str | None
    description: str
    source_column: str | None
    format_string: str | None
    display_folder: str | None
    is_hidden: bool
    is_calculated: bool
    expression: str | None
    lineage_tag: str | None
    source_location: SourceLocation
    properties: tuple[tuple[str, str | bool], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "object_id": self.object_id,
            "table": self.table,
            "name": self.name,
            "data_type": self.data_type,
            "description": self.description,
            "source_column": self.source_column,
            "format_string": self.format_string,
            "display_folder": self.display_folder,
            "is_hidden": self.is_hidden,
            "is_calculated": self.is_calculated,
            "expression": self.expression,
            "lineage_tag": self.lineage_tag,
            "source_location": self.source_location.to_dict(),
            "properties": {key: value for key, value in self.properties},
        }
        if self.is_calculated:
            value["import_support_classification"] = ImportSupportClassification.TARGET_SPECIFIC.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIColumn":
        properties = value.get("properties") or {}
        return cls(
            object_id=str(value["object_id"]), table=str(value["table"]), name=str(value["name"]),
            data_type=str(value["data_type"]) if value.get("data_type") is not None else None,
            description=str(value.get("description", "")),
            source_column=str(value["source_column"]) if value.get("source_column") is not None else None,
            format_string=str(value["format_string"]) if value.get("format_string") is not None else None,
            display_folder=str(value["display_folder"]) if value.get("display_folder") is not None else None,
            is_hidden=bool(value.get("is_hidden")), is_calculated=bool(value.get("is_calculated")),
            expression=str(value["expression"]) if value.get("expression") is not None else None,
            lineage_tag=str(value["lineage_tag"]) if value.get("lineage_tag") is not None else None,
            source_location=SourceLocation.from_dict(value["source_location"]),
            properties=tuple(sorted(((str(k), v) for k, v in properties.items()), key=lambda item: item[0])),
        )


@dataclass(frozen=True)
class PowerBIMeasure:
    object_id: str
    table: str
    name: str
    expression: str
    expression_hash: str
    description: str
    format_string: str | None
    display_folder: str | None
    dynamic_format_string: str | None
    lineage_tag: str | None
    source_location: SourceLocation
    analysis: DaxAnalysis
    dependency_object_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id, "table": self.table, "name": self.name,
            "expression": self.expression, "expression_hash": self.expression_hash,
            "description": self.description, "format_string": self.format_string,
            "display_folder": self.display_folder, "dynamic_format_string": self.dynamic_format_string,
            "lineage_tag": self.lineage_tag, "source_location": self.source_location.to_dict(),
            "analysis": self.analysis.to_dict(), "dependency_object_ids": list(self.dependency_object_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIMeasure":
        return cls(
            object_id=str(value["object_id"]), table=str(value["table"]), name=str(value["name"]),
            expression=str(value.get("expression", "")), expression_hash=str(value["expression_hash"]),
            description=str(value.get("description", "")),
            format_string=str(value["format_string"]) if value.get("format_string") is not None else None,
            display_folder=str(value["display_folder"]) if value.get("display_folder") is not None else None,
            dynamic_format_string=(str(value["dynamic_format_string"]) if value.get("dynamic_format_string") is not None else None),
            lineage_tag=str(value["lineage_tag"]) if value.get("lineage_tag") is not None else None,
            source_location=SourceLocation.from_dict(value["source_location"]),
            analysis=DaxAnalysis.from_dict(value["analysis"]),
            dependency_object_ids=tuple(str(item) for item in value.get("dependency_object_ids", [])),
        )


@dataclass(frozen=True)
class PowerBIPartition:
    object_id: str
    table: str
    name: str
    mode: str | None
    source_kind: str
    source_expression_hash: str
    source_expression_redacted: str
    source_location: SourceLocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id, "table": self.table, "name": self.name, "mode": self.mode,
            "source_kind": self.source_kind, "source_expression_hash": self.source_expression_hash,
            "source_expression_redacted": self.source_expression_redacted,
            "source_location": self.source_location.to_dict(),
            "import_support_classification": ImportSupportClassification.TARGET_SPECIFIC.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIPartition":
        return cls(
            str(value["object_id"]), str(value["table"]), str(value["name"]),
            str(value["mode"]) if value.get("mode") is not None else None,
            str(value["source_kind"]), str(value["source_expression_hash"]),
            str(value.get("source_expression_redacted", "<REDACTED:source-expression>")),
            SourceLocation.from_dict(value["source_location"]),
        )


@dataclass(frozen=True)
class PowerBITable:
    object_id: str
    name: str
    description: str
    lineage_tag: str | None
    is_hidden: bool
    is_calculated: bool
    source_location: SourceLocation
    column_ids: tuple[str, ...] = ()
    measure_ids: tuple[str, ...] = ()
    partition_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "object_id": self.object_id, "name": self.name, "description": self.description,
            "lineage_tag": self.lineage_tag, "is_hidden": self.is_hidden, "is_calculated": self.is_calculated,
            "source_location": self.source_location.to_dict(), "column_ids": list(self.column_ids),
            "measure_ids": list(self.measure_ids), "partition_ids": list(self.partition_ids),
        }
        if self.is_calculated:
            value["import_support_classification"] = (
                ImportSupportClassification.TARGET_SPECIFIC.value
            )
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBITable":
        return cls(
            str(value["object_id"]), str(value["name"]), str(value.get("description", "")),
            str(value["lineage_tag"]) if value.get("lineage_tag") is not None else None,
            bool(value.get("is_hidden")), bool(value.get("is_calculated")),
            SourceLocation.from_dict(value["source_location"]),
            tuple(str(item) for item in value.get("column_ids", [])),
            tuple(str(item) for item in value.get("measure_ids", [])),
            tuple(str(item) for item in value.get("partition_ids", [])),
        )


@dataclass(frozen=True)
class PowerBIRelationship:
    object_id: str
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool
    is_active_explicit: bool
    from_cardinality: str
    from_cardinality_explicit: bool
    to_cardinality: str
    to_cardinality_explicit: bool
    cross_filter_direction: str
    cross_filter_direction_explicit: bool
    source_location: SourceLocation
    raw_properties: tuple[tuple[str, str | bool], ...] = ()

    @property
    def signature(self) -> tuple[str, str, str, str]:
        return tuple(
            item.casefold() for item in (self.from_table, self.from_column, self.to_table, self.to_column)
        )  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id, "name": self.name,
            "from": {"table": self.from_table, "column": self.from_column},
            "to": {"table": self.to_table, "column": self.to_column},
            "is_active": self.is_active, "is_active_explicit": self.is_active_explicit,
            "from_cardinality": self.from_cardinality,
            "from_cardinality_explicit": self.from_cardinality_explicit,
            "to_cardinality": self.to_cardinality, "to_cardinality_explicit": self.to_cardinality_explicit,
            "cross_filter_direction": self.cross_filter_direction,
            "cross_filter_direction_explicit": self.cross_filter_direction_explicit,
            "source_location": self.source_location.to_dict(),
            "raw_properties": {key: value for key, value in self.raw_properties},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIRelationship":
        raw = value.get("raw_properties") or {}
        source = value["from"]
        target = value["to"]
        return cls(
            str(value["object_id"]), str(value["name"]), str(source["table"]), str(source["column"]),
            str(target["table"]), str(target["column"]), bool(value.get("is_active", True)),
            bool(value.get("is_active_explicit")), str(value.get("from_cardinality", "many")),
            bool(value.get("from_cardinality_explicit")), str(value.get("to_cardinality", "one")),
            bool(value.get("to_cardinality_explicit")), str(value.get("cross_filter_direction", "oneDirection")),
            bool(value.get("cross_filter_direction_explicit")), SourceLocation.from_dict(value["source_location"]),
            tuple(sorted(((str(k), v) for k, v in raw.items()), key=lambda item: item[0])),
        )


@dataclass(frozen=True)
class PowerBIHierarchy:
    object_id: str
    table: str
    name: str
    levels: tuple[str, ...]
    source_location: SourceLocation

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "table": self.table, "name": self.name,
                "levels": list(self.levels), "source_location": self.source_location.to_dict(),
                "import_support_classification": ImportSupportClassification.TARGET_SPECIFIC.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIHierarchy":
        return cls(str(value["object_id"]), str(value["table"]), str(value["name"]),
                   tuple(str(item) for item in value.get("levels", [])), SourceLocation.from_dict(value["source_location"]))


@dataclass(frozen=True)
class PowerBICalculationGroup:
    object_id: str
    table: str
    precedence: int | None
    items: tuple[str, ...]
    source_location: SourceLocation

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "table": self.table, "precedence": self.precedence,
                "items": list(self.items), "source_location": self.source_location.to_dict(),
                "import_support_classification": ImportSupportClassification.TARGET_SPECIFIC.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBICalculationGroup":
        return cls(str(value["object_id"]), str(value["table"]),
                   int(value["precedence"]) if value.get("precedence") is not None else None,
                   tuple(str(item) for item in value.get("items", [])), SourceLocation.from_dict(value["source_location"]))


@dataclass(frozen=True)
class PowerBIRole:
    object_id: str
    name: str
    table_permissions: tuple[tuple[str, str], ...]
    source_location: SourceLocation

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "name": self.name,
                "table_permissions": [{"table": table, "filter_expression": expression} for table, expression in self.table_permissions],
                "source_location": self.source_location.to_dict(),
                "import_support_classification": ImportSupportClassification.TARGET_SPECIFIC.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIRole":
        return cls(str(value["object_id"]), str(value["name"]),
                   tuple((str(item["table"]), str(item.get("filter_expression", ""))) for item in value.get("table_permissions", [])),
                   SourceLocation.from_dict(value["source_location"]))


@dataclass(frozen=True)
class PowerBIReportReference:
    object_id: str
    report: str
    object_kind: str
    table: str
    object_name: str
    source_location: SourceLocation

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "report": self.report, "object_kind": self.object_kind,
                "table": self.table, "object_name": self.object_name, "source_location": self.source_location.to_dict(),
                "import_support_classification": ImportSupportClassification.TARGET_SPECIFIC.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIReportReference":
        return cls(str(value["object_id"]), str(value["report"]), str(value["object_kind"]),
                   str(value["table"]), str(value["object_name"]), SourceLocation.from_dict(value["source_location"]))


@dataclass(frozen=True)
class PowerBIModelIdentity:
    object_id: str
    name: str
    tmdl_model_name: str | None
    compatibility_level: int | None
    culture: str | None
    definition_path: str
    source_tree_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "name": self.name, "tmdl_model_name": self.tmdl_model_name,
                "compatibility_level": self.compatibility_level, "culture": self.culture,
                "definition_path": self.definition_path, "source_tree_hash": self.source_tree_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIModelIdentity":
        return cls(str(value["object_id"]), str(value["name"]),
                   str(value["tmdl_model_name"]) if value.get("tmdl_model_name") is not None else None,
                   int(value["compatibility_level"]) if value.get("compatibility_level") is not None else None,
                   str(value["culture"]) if value.get("culture") is not None else None,
                   str(value["definition_path"]), str(value["source_tree_hash"]))


@dataclass(frozen=True)
class PowerBIModelInventory:
    model: PowerBIModelIdentity
    tables: tuple[PowerBITable, ...]
    columns: tuple[PowerBIColumn, ...]
    measures: tuple[PowerBIMeasure, ...]
    relationships: tuple[PowerBIRelationship, ...]
    partitions: tuple[PowerBIPartition, ...]
    hierarchies: tuple[PowerBIHierarchy, ...] = ()
    calculation_groups: tuple[PowerBICalculationGroup, ...] = ()
    roles: tuple[PowerBIRole, ...] = ()
    report_references: tuple[PowerBIReportReference, ...] = ()
    dependency_edges: tuple[tuple[str, str], ...] = ()
    object_support_records: tuple[ObjectSupportRecord, ...] = ()
    diagnostics: tuple[ImportDiagnostic, ...] = ()
    schema_version: int = INVENTORY_SCHEMA_VERSION
    extraction_version: str = EXTRACTION_VERSION

    @property
    def calculated_columns(self) -> tuple[PowerBIColumn, ...]:
        return tuple(item for item in self.columns if item.is_calculated)

    def _payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "extraction_version": self.extraction_version,
            "model": self.model.to_dict(),
            "tables": [item.to_dict() for item in self.tables],
            "columns": [item.to_dict() for item in self.columns],
            "measures": [item.to_dict() for item in self.measures],
            "relationships": [item.to_dict() for item in self.relationships],
            "partitions": [item.to_dict() for item in self.partitions],
            "calculated_columns": [item.object_id for item in self.calculated_columns],
            "hierarchies": [item.to_dict() for item in self.hierarchies],
            "calculation_groups": [item.to_dict() for item in self.calculation_groups],
            "roles": [item.to_dict() for item in self.roles],
            "report_references": [item.to_dict() for item in self.report_references],
            "dependency_graph": {
                "nodes": [item.object_id for item in self.measures],
                "edges": [{"from": source, "to": target} for source, target in self.dependency_edges],
            },
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
        if self.schema_version >= 2:
            payload["object_support_records"] = [
                item.to_dict() for item in self.object_support_records
            ]
        return payload

    @property
    def semantic_hash(self) -> str:
        payload = self._payload()
        model = dict(payload["model"])
        # Provenance paths and byte snapshots are recorded but are not semantic.
        model.pop("definition_path", None)
        model.pop("source_tree_hash", None)
        payload["model"] = model
        return _sha256_value(payload)

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "semantic_hash": self.semantic_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIModelInventory":
        schema_version = int(value.get("schema_version", -1))
        if schema_version not in SUPPORTED_INVENTORY_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported Power BI inventory schema_version: {value.get('schema_version')!r}")
        graph = value.get("dependency_graph") or {}
        result = cls(
            model=PowerBIModelIdentity.from_dict(value["model"]),
            tables=tuple(PowerBITable.from_dict(item) for item in value.get("tables", [])),
            columns=tuple(PowerBIColumn.from_dict(item) for item in value.get("columns", [])),
            measures=tuple(PowerBIMeasure.from_dict(item) for item in value.get("measures", [])),
            relationships=tuple(PowerBIRelationship.from_dict(item) for item in value.get("relationships", [])),
            partitions=tuple(PowerBIPartition.from_dict(item) for item in value.get("partitions", [])),
            hierarchies=tuple(PowerBIHierarchy.from_dict(item) for item in value.get("hierarchies", [])),
            calculation_groups=tuple(PowerBICalculationGroup.from_dict(item) for item in value.get("calculation_groups", [])),
            roles=tuple(PowerBIRole.from_dict(item) for item in value.get("roles", [])),
            report_references=tuple(PowerBIReportReference.from_dict(item) for item in value.get("report_references", [])),
            dependency_edges=tuple((str(item["from"]), str(item["to"])) for item in graph.get("edges", [])),
            object_support_records=tuple(
                ObjectSupportRecord.from_dict(item)
                for item in value.get("object_support_records", [])
            ),
            diagnostics=tuple(ImportDiagnostic.from_dict(item) for item in value.get("diagnostics", [])),
            schema_version=schema_version, extraction_version=str(value.get("extraction_version", EXTRACTION_VERSION)),
        )
        supplied_hash = value.get("semantic_hash")
        if supplied_hash is not None and supplied_hash != result.semantic_hash:
            raise ValueError("Power BI inventory semantic_hash does not match its content.")
        if schema_version >= 2:
            expected_object_ids = {
                result.model.object_id,
                *(item.object_id for item in result.tables),
                *(item.object_id for item in result.columns),
                *(item.object_id for item in result.measures),
                *(item.object_id for item in result.relationships),
                *(item.object_id for item in result.partitions),
                *(item.object_id for item in result.hierarchies),
                *(item.object_id for item in result.calculation_groups),
                *(item.object_id for item in result.roles),
                *(item.object_id for item in result.report_references),
            }
            support_object_ids = [
                item.object_id for item in result.object_support_records
            ]
            if (
                len(support_object_ids) != len(set(support_object_ids))
                or set(support_object_ids) != expected_object_ids
            ):
                raise ValueError(
                    "Schema-v2 inventory must contain exactly one support record for every object."
                )
        return result


@dataclass
class _ParsedTable:
    table: PowerBITable
    columns: list[PowerBIColumn] = field(default_factory=list)
    measures: list[PowerBIMeasure] = field(default_factory=list)
    partitions: list[PowerBIPartition] = field(default_factory=list)
    hierarchies: list[PowerBIHierarchy] = field(default_factory=list)
    calculation_group: PowerBICalculationGroup | None = None


def _block_end(lines: Sequence[str], start: int, indent: int) -> int:
    index = start + 1
    while index < len(lines):
        if lines[index].strip() and _indent_width(lines[index]) <= indent:
            break
        index += 1
    return index


def _description(pending: Sequence[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(pending)).strip()


def _property_map(lines: Sequence[str], start: int, end: int, declaration_indent: int) -> dict[str, str | bool]:
    properties: dict[str, str | bool] = {}
    for line in lines[start + 1:end]:
        if not line.strip() or _indent_width(line) <= declaration_indent:
            continue
        stripped = line.strip()
        if stripped.startswith("///") or stripped.startswith("annotation ") or stripped.startswith("changedProperty"):
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                properties[key] = _clean_value(value) or ""
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", stripped):
            properties[stripped] = True
    return properties


def _expression_lines(
    lines: Sequence[str],
    start: int,
    end: int,
    declaration_indent: int,
    first_expression: str,
) -> str:
    parts = [first_expression.strip()] if first_expression.strip() else []
    property_indent: int | None = None
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("///"):
            continue
        indent = _indent_width(line)
        if indent <= declaration_indent:
            break
        if re.match(r"^(?:formatString|displayFolder|lineageTag|dataType|sourceColumn|summarizeBy|mode|source|expression|detailRowsExpression):", stripped):
            property_indent = indent
            continue
        if stripped.startswith("annotation ") or stripped.startswith("changedProperty"):
            property_indent = indent if property_indent is None else property_indent
            continue
        if property_indent is None or indent > property_indent:
            parts.append(stripped)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _source_kind(expression: str, partition_kind: str) -> str:
    upper = expression.upper()
    if partition_kind.casefold() == "calculated" or upper.startswith("CALCULATE"):
        return "CALCULATED"
    if "SQL.DATABASE(" in upper:
        return "SQL_DATABASE"
    if "TABLE.FROMROWS(" in upper:
        return "INLINE_TABLE"
    if partition_kind.casefold() == "entity":
        return "ENTITY"
    return "M_EXPRESSION" if partition_kind.casefold() == "m" else partition_kind.upper()


def _parse_table_file(path: Path, definition: Path) -> _ParsedTable:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    relative = path.relative_to(definition).as_posix()
    positive_indents = [
        _indent_width(line)
        for line in lines
        if line.strip() and _indent_width(line) > 0
    ]
    child_indent = min(positive_indents) if positive_indents else 4
    table_name = path.stem
    table_line = 1
    table_description = ""
    table_lineage: str | None = None
    table_hidden = False
    table_calculated = False
    pending: list[str] = []
    columns: list[PowerBIColumn] = []
    raw_measures: list[tuple[dict[str, Any], SourceLocation]] = []
    partitions: list[PowerBIPartition] = []
    hierarchies: list[PowerBIHierarchy] = []
    calculation_group: PowerBICalculationGroup | None = None
    table_declaration_found = False
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        indent = _indent_width(line)
        if stripped.startswith("///"):
            pending.append(stripped[3:].strip())
            index += 1
            continue
        table_declaration = _tmdl_declaration(stripped, "table") if indent == 0 else None
        if table_declaration:
            if table_declaration_found:
                raise PowerBIImportError(f"Multiple table declarations in {relative}.")
            table_declaration_found = True
            table_name, table_expression = table_declaration
            table_line = index + 1
            table_description = _description(pending)
            pending = []
            if table_expression is not None:
                table_calculated = True
            index += 1
            continue
        if indent == child_indent and stripped.startswith("lineageTag:"):
            table_lineage = stripped.split(":", 1)[1].strip()
            index += 1
            continue
        if indent == child_indent and stripped == "isHidden":
            table_hidden = True
            index += 1
            continue
        measure_declaration = _tmdl_declaration(stripped, "measure") if indent == child_indent else None
        if measure_declaration and measure_declaration[1] is not None:
            measure_name, measure_expression = measure_declaration
            end = _block_end(lines, index, indent)
            props = _property_map(lines, index, end, indent)
            expression = _expression_lines(lines, index, end, indent, measure_expression or "")
            has_format_definition = any(
                child.strip().startswith(("formatStringDefinition", "formatStringExpression", "dynamicFormatString"))
                for child in lines[index + 1:end]
            )
            dynamic = props.get("formatStringExpression") or props.get("dynamicFormatString") or (
                "<DYNAMIC_FORMAT_STRING>" if has_format_definition else None
            )
            raw_measures.append(({
                "name": measure_name, "expression": expression,
                "description": _description(pending), "format_string": props.get("formatString"),
                "display_folder": props.get("displayFolder"), "dynamic_format_string": dynamic,
                "lineage_tag": props.get("lineageTag"),
            }, SourceLocation(relative, index + 1, indent + 1, end)))
            pending = []
            index = end
            continue
        column_declaration = _tmdl_declaration(stripped, "column") if indent == child_indent else None
        if column_declaration:
            column_name, first_expression = column_declaration
            end = _block_end(lines, index, indent)
            props = _property_map(lines, index, end, indent)
            expression = _expression_lines(lines, index, end, indent, first_expression or "") if first_expression is not None else None
            name = column_name
            columns.append(PowerBIColumn(
                object_id=_object_id("column", table_name, name), table=table_name, name=name,
                data_type=str(props["dataType"]) if props.get("dataType") is not None else None,
                description=_description(pending),
                source_column=str(props["sourceColumn"]) if props.get("sourceColumn") is not None else None,
                format_string=str(props["formatString"]) if props.get("formatString") is not None else None,
                display_folder=str(props["displayFolder"]) if props.get("displayFolder") is not None else None,
                is_hidden=props.get("isHidden") is True or str(props.get("isHidden", "")).casefold() == "true",
                is_calculated=first_expression is not None, expression=expression,
                lineage_tag=str(props["lineageTag"]) if props.get("lineageTag") is not None else None,
                source_location=SourceLocation(relative, index + 1, indent + 1, end),
                properties=tuple(sorted(props.items(), key=lambda item: item[0])),
            ))
            pending = []
            index = end
            continue
        partition_declaration = _tmdl_declaration(stripped, "partition") if indent == child_indent else None
        if partition_declaration and partition_declaration[1] is not None and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*", partition_declaration[1]
        ):
            end = _block_end(lines, index, indent)
            props = _property_map(lines, index, end, indent)
            expression = _expression_lines(lines, index, end, indent, "")
            # _expression_lines intentionally skips the source property and keeps
            # its deeper M body. Hash the body; never retain it in inventory.
            name, kind = partition_declaration
            partitions.append(PowerBIPartition(
                _object_id("partition", table_name, name), table_name, name,
                str(props["mode"]) if props.get("mode") is not None else None,
                _source_kind(expression, kind), hashlib.sha256(expression.encode("utf-8")).hexdigest(),
                "<REDACTED:source-expression>", SourceLocation(relative, index + 1, indent + 1, end),
            ))
            pending = []
            index = end
            continue
        hierarchy_declaration = _tmdl_declaration(stripped, "hierarchy") if indent == child_indent else None
        if hierarchy_declaration:
            end = _block_end(lines, index, indent)
            levels = []
            for child in lines[index + 1:end]:
                level_declaration = _tmdl_declaration(child.strip(), "level")
                if level_declaration:
                    levels.append(level_declaration[0])
            name = hierarchy_declaration[0]
            hierarchies.append(PowerBIHierarchy(_object_id("hierarchy", table_name, name), table_name, name,
                                                  tuple(levels), SourceLocation(relative, index + 1, indent + 1, end)))
            pending = []
            index = end
            continue
        if indent == child_indent and stripped.startswith("calculationGroup"):
            end = _block_end(lines, index, indent)
            precedence: int | None = None
            items: list[str] = []
            for child in lines[index + 1:end]:
                child_stripped = child.strip()
                if child_stripped.startswith("precedence:"):
                    try:
                        precedence = int(child_stripped.split(":", 1)[1].strip())
                    except ValueError:
                        precedence = None
                item_declaration = _tmdl_declaration(child_stripped, "calculationItem")
                if item_declaration:
                    items.append(item_declaration[0])
            calculation_group = PowerBICalculationGroup(
                _object_id("calculation_group", table_name), table_name, precedence, tuple(items),
                SourceLocation(relative, index + 1, indent + 1, end),
            )
            index = end
            continue
        if stripped:
            pending = []
        index += 1

    if not table_declaration_found:
        raise PowerBIImportError(f"TMDL table file has no top-level table declaration: {relative}")

    def assert_unique(names: Iterable[str], label: str) -> None:
        seen: set[str] = set()
        for name in names:
            normalized = name.casefold()
            if normalized in seen:
                raise PowerBIImportError(f"Duplicate {label} identity {name!r} in table {table_name!r}.")
            seen.add(normalized)

    assert_unique((item.name for item in columns), "column")
    assert_unique((str(item[0]["name"]) for item in raw_measures), "measure")
    assert_unique((item.name for item in partitions), "partition")
    assert_unique((item.name for item in hierarchies), "hierarchy")
    if calculation_group is not None:
        assert_unique(calculation_group.items, "calculation item")

    measure_names = [item[0]["name"] for item in raw_measures]
    measures = []
    for raw, location in raw_measures:
        analysis = analyze_dax_measure(
            raw["expression"], known_measure_names=measure_names,
            has_dynamic_format_string=raw["dynamic_format_string"] is not None,
            calculation_group_context=calculation_group is not None,
        )
        measures.append(PowerBIMeasure(
            object_id=_object_id("measure", table_name, raw["name"]), table=table_name, name=raw["name"],
            expression=raw["expression"], expression_hash=hashlib.sha256(raw["expression"].encode("utf-8")).hexdigest(),
            description=raw["description"],
            format_string=str(raw["format_string"]) if raw["format_string"] is not None else None,
            display_folder=str(raw["display_folder"]) if raw["display_folder"] is not None else None,
            dynamic_format_string=(str(raw["dynamic_format_string"]) if raw["dynamic_format_string"] is not None else None),
            lineage_tag=str(raw["lineage_tag"]) if raw["lineage_tag"] is not None else None,
            source_location=location, analysis=analysis,
        ))
    table = PowerBITable(
        _object_id("table", table_name), table_name, table_description, table_lineage, table_hidden, table_calculated,
        SourceLocation(relative, table_line, 1),
        tuple(item.object_id for item in sorted(columns, key=lambda item: item.name.casefold())),
        tuple(item.object_id for item in sorted(measures, key=lambda item: item.name.casefold())),
        tuple(item.object_id for item in sorted(partitions, key=lambda item: item.name.casefold())),
    )
    return _ParsedTable(table, columns, measures, partitions, hierarchies, calculation_group)


def _parse_relationships(path: Path, definition: Path) -> tuple[PowerBIRelationship, ...]:
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    relative = path.relative_to(definition).as_posix()
    relationships: list[PowerBIRelationship] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        indent = _indent_width(line)
        match = re.match(r"^relationship\s+(.+)$", stripped) if indent == 0 else None
        if not match:
            index += 1
            continue
        end = _block_end(lines, index, indent)
        props = _property_map(lines, index, end, indent)
        source = str(props.get("fromColumn", ""))
        target = str(props.get("toColumn", ""))
        if "." not in source or "." not in target:
            raise PowerBIImportError(f"Relationship {match.group(1)!r} has incomplete endpoints at {relative}:{index + 1}.")
        source_table, source_column = _split_qualified_tmdl_reference(source)
        target_table, target_column = _split_qualified_tmdl_reference(target)
        active_key = "isActive" if "isActive" in props else "active" if "active" in props else None
        active_raw = props.get(active_key) if active_key else None
        active = not (active_raw is False or str(active_raw).casefold() == "false")
        from_key = "fromCardinality"
        to_key = "toCardinality"
        filter_key = "crossFilteringBehavior" if "crossFilteringBehavior" in props else "crossFilterDirection" if "crossFilterDirection" in props else None
        name = _clean_identifier(match.group(1))
        relationships.append(PowerBIRelationship(
            _object_id("relationship", name, source_table, source_column, target_table, target_column), name,
            source_table, source_column, target_table, target_column,
            active, active_key is not None,
            str(props.get(from_key, "many")), from_key in props,
            str(props.get(to_key, "one")), to_key in props,
            str(props.get(filter_key, "oneDirection")), filter_key is not None,
            SourceLocation(relative, index + 1, 1, end), tuple(sorted(props.items(), key=lambda item: item[0])),
        ))
        index = end
    seen_names: set[str] = set()
    seen_signatures: set[tuple[str, str, str, str]] = set()
    for relationship in relationships:
        if relationship.name.casefold() in seen_names:
            raise PowerBIImportError(f"Duplicate relationship identity: {relationship.name!r}")
        if relationship.signature in seen_signatures:
            raise PowerBIImportError(
                "Ambiguous duplicate relationship endpoints: "
                f"{relationship.from_table}[{relationship.from_column}] -> "
                f"{relationship.to_table}[{relationship.to_column}]"
            )
        seen_names.add(relationship.name.casefold())
        seen_signatures.add(relationship.signature)
    return tuple(sorted(relationships, key=lambda item: (item.name.casefold(), item.object_id)))


def _parse_roles(definition: Path) -> tuple[PowerBIRole, ...]:
    role_dir = definition / "roles"
    if not role_dir.exists():
        return ()
    roles: list[PowerBIRole] = []
    for path in sorted(role_dir.glob("*.tmdl"), key=lambda item: item.name.casefold()):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        relative = path.relative_to(definition).as_posix()
        role_name = path.stem
        role_line = 1
        permissions: list[tuple[str, str]] = []
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            role_match = re.match(r"^role\s+(.+)$", stripped)
            if role_match:
                role_name = _clean_identifier(role_match.group(1))
                role_line = index + 1
            permission_match = re.match(r"^tablePermission\s+(.+)$", stripped)
            if permission_match:
                indent = _indent_width(lines[index])
                end = _block_end(lines, index, indent)
                props = _property_map(lines, index, end, indent)
                permissions.append((_clean_identifier(permission_match.group(1)), str(props.get("filterExpression", ""))))
                index = end
                continue
            index += 1
        roles.append(PowerBIRole(_object_id("role", role_name), role_name, tuple(sorted(permissions)),
                                 SourceLocation(relative, role_line, 1)))
    return tuple(sorted(roles, key=lambda item: item.name.casefold()))


def _walk_report_fields(value: Any) -> Iterator[tuple[str, str, str]]:
    if isinstance(value, Mapping):
        for kind in ("Column", "Measure"):
            candidate = value.get(kind)
            if isinstance(candidate, Mapping):
                prop = candidate.get("Property")
                expression = candidate.get("Expression")
                if isinstance(prop, str) and isinstance(expression, Mapping):
                    source_ref = expression.get("SourceRef")
                    if isinstance(source_ref, Mapping) and isinstance(source_ref.get("Entity"), str):
                        yield kind.upper(), str(source_ref["Entity"]), prop
        for child in value.values():
            yield from _walk_report_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_report_fields(child)


def _report_directories(definition: Path, repository_root: Path) -> tuple[Path, ...]:
    model_dir = definition.parent
    reports: list[Path] = []
    for raw_pbir_path in repository_root.rglob("*.Report/definition.pbir"):
        try:
            pbir_path = _ensure_contained(
                raw_pbir_path, repository_root, label="PBIR definition"
            )
            pbir = json.loads(pbir_path.read_text(encoding="utf-8-sig"))
            by_path = ((pbir.get("datasetReference") or {}).get("byPath") or {})
            target = by_path.get("path") if isinstance(by_path, Mapping) else None
            if isinstance(target, str) and (pbir_path.parent / target).resolve() == model_dir.resolve():
                reports.append(pbir_path.parent)
        except (OSError, json.JSONDecodeError):
            continue
    return tuple(sorted(set(reports), key=lambda item: item.as_posix().casefold()))


def _parse_report_references(definition: Path, repository_root: Path) -> tuple[PowerBIReportReference, ...]:
    references: dict[tuple[str, str, str, str], PowerBIReportReference] = {}
    for report_dir in _report_directories(definition, repository_root):
        raw_definition_dir = report_dir / "definition"
        if not raw_definition_dir.exists():
            continue
        definition_dir = _ensure_contained(
            raw_definition_dir, repository_root, label="Power BI report definition"
        )
        _validate_contained_tree(
            definition_dir, repository_root, label="Power BI report definition"
        )
        if not definition_dir.exists():
            continue
        report_name = report_dir.name[:-7] if report_dir.name.casefold().endswith(".report") else report_dir.name
        for raw_path in sorted(definition_dir.rglob("*.json"), key=lambda item: item.as_posix().casefold()):
            try:
                path = _ensure_contained(
                    raw_path, repository_root, label="Power BI report JSON"
                )
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            source_file = _relative_posix(path, repository_root)
            for kind, table, name in _walk_report_fields(value):
                key = (report_name.casefold(), kind, table.casefold(), name.casefold())
                references.setdefault(key, PowerBIReportReference(
                    _object_id("report_ref", report_name, kind, table, name), report_name, kind, table, name,
                    SourceLocation(source_file, 1, 1),
                ))
    return tuple(sorted(references.values(), key=lambda item: (item.report.casefold(), item.table.casefold(), item.object_name.casefold(), item.object_kind)))


def _model_identity(definition: Path, repository_root: Path) -> PowerBIModelIdentity:
    database_text = (definition / "database.tmdl").read_text(encoding="utf-8-sig")
    model_text = (definition / "model.tmdl").read_text(encoding="utf-8-sig")
    compatibility_match = re.search(r"^\s*compatibilityLevel:\s*(\d+)\s*$", database_text, re.MULTILINE)
    model_match = re.search(r"^model\s+(.+)$", model_text, re.MULTILINE)
    culture_match = re.search(r"^\s*culture:\s*(.+)$", model_text, re.MULTILINE)
    directory_name = definition.parent.name
    name = directory_name[: -len(".SemanticModel")] if directory_name.casefold().endswith(".semanticmodel") else directory_name
    return PowerBIModelIdentity(
        _object_id("model", name), name,
        _clean_identifier(model_match.group(1)) if model_match else None,
        int(compatibility_match.group(1)) if compatibility_match else None,
        _clean_value(culture_match.group(1)) if culture_match else None,
        _relative_posix(definition, repository_root), _tree_hash(definition),
    )


def _resolve_measure_dependencies(measures: Sequence[PowerBIMeasure]) -> tuple[tuple[PowerBIMeasure, ...], tuple[tuple[str, str], ...], tuple[ImportDiagnostic, ...]]:
    by_name: dict[str, list[PowerBIMeasure]] = {}
    for measure in measures:
        by_name.setdefault(measure.name.casefold(), []).append(measure)
    updated: list[PowerBIMeasure] = []
    edges: set[tuple[str, str]] = set()
    diagnostics: list[ImportDiagnostic] = []
    for measure in measures:
        targets: list[str] = []
        local_diagnostics = list(measure.analysis.diagnostics)
        for dependency in measure.analysis.measure_dependencies:
            matches = by_name.get(dependency.casefold(), [])
            if len(matches) == 1:
                targets.append(matches[0].object_id)
                edges.add((measure.object_id, matches[0].object_id))
            elif len(matches) > 1:
                local_diagnostics.append(ImportDiagnostic(
                    "DAX_AMBIGUOUS_MEASURE_REFERENCE",
                    f"Measure reference [{dependency}] resolves to {len(matches)} measures.",
                    source_location=measure.source_location,
                ))
            else:
                local_diagnostics.append(ImportDiagnostic(
                    "DAX_MEASURE_REFERENCE_MISSING", f"Measure reference [{dependency}] does not resolve.",
                    source_location=measure.source_location,
                ))
        analysis = replace(measure.analysis, supported=measure.analysis.supported and not local_diagnostics,
                           diagnostics=tuple(local_diagnostics))
        updated.append(replace(measure, analysis=analysis, dependency_object_ids=tuple(sorted(targets))))

    graph: dict[str, list[str]] = {measure.object_id: [] for measure in measures}
    for source, target in edges:
        graph[source].append(target)
    state: dict[str, int] = {}
    stack: list[str] = []
    cycle_nodes: set[str] = set()

    def visit(node: str) -> None:
        if state.get(node) == 1:
            cycle = stack[stack.index(node):] + [node]
            cycle_nodes.update(cycle)
            diagnostics.append(ImportDiagnostic("DAX_MEASURE_DEPENDENCY_CYCLE", f"Measure dependency cycle: {' -> '.join(cycle)}."))
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        stack.append(node)
        for target in sorted(graph[node]):
            visit(target)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        visit(node)
    if cycle_nodes:
        cycle_message = "Measure participates in a dependency cycle; canonicalization is unsafe."
        cycle_diagnostic = ImportDiagnostic("DAX_MEASURE_DEPENDENCY_CYCLE", cycle_message)
        updated = [
            replace(
                measure,
                analysis=replace(
                    measure.analysis,
                    supported=False,
                    diagnostics=measure.analysis.diagnostics + (cycle_diagnostic,),
                ),
            )
            if measure.object_id in cycle_nodes
            else measure
            for measure in updated
        ]
    return tuple(updated), tuple(sorted(edges)), tuple(diagnostics)


def extract_powerbi_inventory(
    model_path: str | Path,
    repository_root: str | Path | None = None,
) -> PowerBIModelInventory:
    """Extract a deterministic inventory without modifying source files."""

    root = Path(repository_root or Path.cwd()).resolve(strict=True)
    definition = resolve_powerbi_model_dir(model_path, root)
    _validate_contained_tree(definition, root, label="TMDL definition")
    parsed = [_parse_table_file(path, definition) for path in sorted((definition / "tables").glob("*.tmdl"), key=lambda item: item.name.casefold())]
    table_names = [item.table.name.casefold() for item in parsed]
    if len(table_names) != len(set(table_names)):
        raise PowerBIImportError("TMDL definition contains duplicate case-insensitive table identities.")
    raw_measures = [measure for table in parsed for measure in table.measures]
    all_measure_names = [measure.name for measure in raw_measures]
    calculation_group_tables = {
        item.calculation_group.table.casefold() for item in parsed if item.calculation_group is not None
    }
    globally_analyzed = [
        replace(
            measure,
            analysis=analyze_dax_measure(
                measure.expression,
                known_measure_names=all_measure_names,
                has_dynamic_format_string=measure.dynamic_format_string is not None,
                calculation_group_context=measure.table.casefold() in calculation_group_tables,
            ),
        )
        for measure in raw_measures
    ]
    measures, edges, dependency_diagnostics = _resolve_measure_dependencies(globally_analyzed)
    measure_by_id = {item.object_id: item for item in measures}
    # Replace table-local measure objects with their dependency-resolved versions.
    tables = tuple(sorted((item.table for item in parsed), key=lambda item: item.name.casefold()))
    columns = tuple(sorted((column for item in parsed for column in item.columns), key=lambda item: (item.table.casefold(), item.name.casefold())))
    partitions = tuple(sorted((partition for item in parsed for partition in item.partitions), key=lambda item: (item.table.casefold(), item.name.casefold())))
    hierarchies = tuple(sorted((hierarchy for item in parsed for hierarchy in item.hierarchies), key=lambda item: (item.table.casefold(), item.name.casefold())))
    calculation_groups = tuple(sorted((item.calculation_group for item in parsed if item.calculation_group is not None), key=lambda item: item.table.casefold()))
    inventory = PowerBIModelInventory(
        model=_model_identity(definition, root), tables=tables, columns=columns,
        measures=tuple(sorted(measure_by_id.values(), key=lambda item: (item.table.casefold(), item.name.casefold()))),
        relationships=_parse_relationships(definition / "relationships.tmdl", definition), partitions=partitions,
        hierarchies=hierarchies, calculation_groups=calculation_groups, roles=_parse_roles(definition),
        report_references=_parse_report_references(definition, root), dependency_edges=edges,
        diagnostics=dependency_diagnostics,
    )
    return replace(
        inventory,
        object_support_records=build_object_support_records(inventory),
    )


def render_inventory_markdown(inventory: PowerBIModelInventory) -> str:
    def cell(value: Any, *, empty: str = "-") -> str:
        if value is None or value == "":
            return empty
        if isinstance(value, bool):
            rendered = str(value).lower()
        else:
            rendered = str(value)
        return rendered.replace("\r", " ").replace("\n", " ").replace("|", "\\|")

    def location(value: SourceLocation) -> str:
        return f"{cell(value.file)}:{value.line}"

    counts = {
        "Tables": len(inventory.tables), "Columns": len(inventory.columns), "Measures": len(inventory.measures),
        "Calculated columns": len(inventory.calculated_columns),
        "Relationships": len(inventory.relationships), "Partitions": len(inventory.partitions),
        "Hierarchies": len(inventory.hierarchies), "Calculation groups": len(inventory.calculation_groups),
        "Roles": len(inventory.roles), "Dependency nodes": len(inventory.measures),
        "Dependency edges": len(inventory.dependency_edges), "Report references": len(inventory.report_references),
        "Object support records": len(inventory.object_support_records),
        "Diagnostics": len(inventory.diagnostics),
    }
    lines = [
        f"# Power BI inventory: {cell(inventory.model.name)}",
        "",
        f"Semantic hash: `{inventory.semantic_hash}`",
        "",
        "## Model",
        "",
        f"- Object ID: `{inventory.model.object_id}`",
        f"- TMDL model name: {cell(inventory.model.tmdl_model_name)}",
        f"- Compatibility level: {cell(inventory.model.compatibility_level)}",
        f"- Culture: {cell(inventory.model.culture)}",
        f"- Definition path: `{cell(inventory.model.definition_path)}`",
        f"- Source tree hash: `{inventory.model.source_tree_hash}`",
        "",
        "## Summary",
        "",
    ]
    lines.extend(f"- {name}: {value}" for name, value in counts.items())
    lines.extend([
        "", "## Tables", "",
        "| Table | Columns | Measures | Partitions | Hidden | Calculated | Description | Source |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ])
    for table in inventory.tables:
        lines.append(
            f"| {cell(table.name)} | {len(table.column_ids)} | {len(table.measure_ids)} | "
            f"{len(table.partition_ids)} | {cell(table.is_hidden)} | {cell(table.is_calculated)} | "
            f"{cell(table.description)} | {location(table.source_location)} |"
        )
    lines.extend([
        "", "## Columns", "",
        "| Table | Column | Type | Source column | Calculated | Hidden | Folder | Format | Description | Source |",
        "|---|---|---|---|---:|---:|---|---|---|---|",
    ])
    for column in inventory.columns:
        lines.append(
            f"| {cell(column.table)} | {cell(column.name)} | {cell(column.data_type)} | "
            f"{cell(column.source_column)} | {cell(column.is_calculated)} | {cell(column.is_hidden)} | "
            f"{cell(column.display_folder)} | {cell(column.format_string)} | {cell(column.description)} | "
            f"{location(column.source_location)} |"
        )
    lines.extend([
        "", "## Measures", "",
        "| Table | Measure | Pattern | Parser support | Dependencies | Folder | Format | Description | Source |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for measure in inventory.measures:
        dependencies = ", ".join(measure.analysis.measure_dependencies) or "-"
        support = "supported" if measure.analysis.supported else ", ".join(item.code for item in measure.analysis.diagnostics)
        lines.append(
            f"| {cell(measure.table)} | {cell(measure.name)} | {cell(measure.analysis.pattern)} | "
            f"{cell(support)} | {cell(dependencies)} | {cell(measure.display_folder)} | "
            f"{cell(measure.format_string)} | {cell(measure.description)} | {location(measure.source_location)} |"
        )
    lines.extend([
        "", "## Relationships", "",
        "| From | To | Active | Cardinality | Filter direction | Explicit properties | Source |",
        "|---|---|---:|---|---|---|---|",
    ])
    for relationship in inventory.relationships:
        explicit = ", ".join(key for key, _ in relationship.raw_properties) or "none"
        lines.append(
            f"| {cell(relationship.from_table)}[{cell(relationship.from_column)}] | "
            f"{cell(relationship.to_table)}[{cell(relationship.to_column)}] | "
            f"{cell(relationship.is_active)} | {cell(relationship.from_cardinality)} to "
            f"{cell(relationship.to_cardinality)} | {cell(relationship.cross_filter_direction)} | "
            f"{cell(explicit)} | {location(relationship.source_location)} |"
        )
    lines.extend([
        "", "## Partitions", "",
        "| Table | Partition | Mode | Source kind | Redacted source | Source hash | Classification | Source |",
        "|---|---|---|---|---|---|---|---|",
    ])
    for partition in inventory.partitions:
        lines.append(
            f"| {cell(partition.table)} | {cell(partition.name)} | {cell(partition.mode)} | "
            f"{cell(partition.source_kind)} | {cell(partition.source_expression_redacted)} | "
            f"`{partition.source_expression_hash}` | {ImportSupportClassification.TARGET_SPECIFIC.value} | "
            f"{location(partition.source_location)} |"
        )

    lines.extend([
        "", "## Hierarchies", "",
        "| Table | Hierarchy | Levels | Classification | Source |",
        "|---|---|---|---|---|",
    ])
    for hierarchy in inventory.hierarchies:
        lines.append(
            f"| {cell(hierarchy.table)} | {cell(hierarchy.name)} | {cell(', '.join(hierarchy.levels))} | "
            f"{ImportSupportClassification.TARGET_SPECIFIC.value} | {location(hierarchy.source_location)} |"
        )
    if not inventory.hierarchies:
        lines.append("| - | - | - | TARGET_SPECIFIC | - |")

    lines.extend([
        "", "## Calculation groups", "",
        "| Table | Precedence | Items | Classification | Source |",
        "|---|---:|---|---|---|",
    ])
    for group in inventory.calculation_groups:
        lines.append(
            f"| {cell(group.table)} | {cell(group.precedence)} | {cell(', '.join(group.items))} | "
            f"{ImportSupportClassification.TARGET_SPECIFIC.value} | {location(group.source_location)} |"
        )
    if not inventory.calculation_groups:
        lines.append("| - | - | - | TARGET_SPECIFIC | - |")

    lines.extend([
        "", "## Roles and RLS", "",
        "| Role | Table permissions | Classification | Source |",
        "|---|---|---|---|",
    ])
    for role in inventory.roles:
        permissions = "; ".join(f"{table}: {expression}" for table, expression in role.table_permissions)
        lines.append(
            f"| {cell(role.name)} | {cell(permissions)} | {ImportSupportClassification.TARGET_SPECIFIC.value} | "
            f"{location(role.source_location)} |"
        )
    if not inventory.roles:
        lines.append("| - | - | TARGET_SPECIFIC | - |")

    lines.extend([
        "", "## Report references", "",
        "| Report | Kind | Object | Classification | Source |",
        "|---|---|---|---|---|",
    ])
    for reference in inventory.report_references:
        lines.append(
            f"| {cell(reference.report)} | {cell(reference.object_kind)} | "
            f"{cell(reference.table)}[{cell(reference.object_name)}] | "
            f"{ImportSupportClassification.TARGET_SPECIFIC.value} | {location(reference.source_location)} |"
        )
    if not inventory.report_references:
        lines.append("| - | - | - | TARGET_SPECIFIC | - |")

    measure_names = {item.object_id: f"{item.table}[{item.name}]" for item in inventory.measures}
    lines.extend([
        "", "## Dependency graph", "",
        "| From | To |", "|---|---|",
    ])
    for source, target in inventory.dependency_edges:
        lines.append(f"| {cell(measure_names.get(source, source))} | {cell(measure_names.get(target, target))} |")
    if not inventory.dependency_edges:
        lines.append("| - | - |")

    lines.extend([
        "", "## Object support classifications", "",
        "| Kind | Object | Classification | Confidence | Rule | Dependencies | Required mappings | Assumptions | Unsupported constructs | Source |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for record in inventory.object_support_records:
        record_location = location(record.source_location) if record.source_location else "-"
        lines.append(
            f"| {cell(record.object_kind)} | {cell(record.object_name)} | "
            f"{record.classification.value} | {record.confidence.value} | "
            f"{cell(record.classifier_rule_id)} | {cell(', '.join(record.dependencies))} | "
            f"{cell(', '.join(record.required_mappings))} | {cell(', '.join(record.assumptions))} | "
            f"{cell(', '.join(record.unsupported_constructs))} | {record_location} |"
        )
    if not inventory.object_support_records:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")

    lines.extend([
        "", "## Inventory diagnostics", "",
        "| Severity | Code | Message | Source |", "|---|---|---|---|",
    ])
    for diagnostic in inventory.diagnostics:
        diagnostic_location = location(diagnostic.source_location) if diagnostic.source_location else "-"
        lines.append(
            f"| {cell(diagnostic.severity)} | {cell(diagnostic.code)} | {cell(diagnostic.message)} | "
            f"{diagnostic_location} |"
        )
    if not inventory.diagnostics:
        lines.append("| - | - | - | - |")
    return "\n".join(lines) + "\n"


def inventory_json_bytes(inventory: PowerBIModelInventory) -> bytes:
    """Return stable, pretty schema-v1 JSON suitable for immutable storage."""

    return (json.dumps(inventory.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _load_mapping(value: str | Path | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return {"schema_version": MAPPING_SCHEMA_VERSION, "tables": [], "columns": [], "measures": []}
    if isinstance(value, Mapping):
        return value
    path = Path(value)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise MappingValidationError(f"Cannot read mapping file {path}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise MappingValidationError("Mapping file must contain an object.")
    return loaded


def validate_import_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed_top = {"schema_version", "tables", "columns", "measures"}
    unknown = set(value) - allowed_top
    missing_top = allowed_top - set(value)
    if unknown or missing_top:
        details = []
        if unknown:
            details.append(f"unknown: {', '.join(sorted(unknown))}")
        if missing_top:
            details.append(f"missing: {', '.join(sorted(missing_top))}")
        raise MappingValidationError("Invalid mapping document keys (" + "; ".join(details) + ").")
    if value.get("schema_version") != MAPPING_SCHEMA_VERSION:
        raise MappingValidationError(f"mapping schema_version must be {MAPPING_SCHEMA_VERSION}.")
    result: dict[str, Any] = {"schema_version": MAPPING_SCHEMA_VERSION}
    specifications = {
        "tables": ({"power_bi_table", "canonical_semantic_model", "canonical_entity", "snowflake_logical_table"}, {"power_bi_table"}),
        "columns": ({"power_bi_table", "power_bi_column", "canonical_semantic_model", "canonical_field"}, {"power_bi_table", "power_bi_column", "canonical_field"}),
        "measures": ({"power_bi_table", "power_bi_measure", "canonical_metric", "snowflake_logical_table", "snowflake_metric"}, {"power_bi_table", "power_bi_measure", "canonical_metric"}),
    }
    for collection, (allowed, required) in specifications.items():
        raw_items = value.get(collection, [])
        if not isinstance(raw_items, list):
            raise MappingValidationError(f"{collection} must be an array.")
        items: list[dict[str, str]] = []
        seen: set[tuple[str, ...]] = set()
        identity_keys = ("power_bi_table", "power_bi_measure") if collection == "measures" else (
            ("power_bi_table", "power_bi_column") if collection == "columns" else ("power_bi_table",)
        )
        for index, item in enumerate(raw_items):
            if not isinstance(item, Mapping):
                raise MappingValidationError(f"{collection}[{index}] must be an object.")
            item_unknown = set(item) - allowed
            missing = required - set(item)
            if item_unknown or missing:
                raise MappingValidationError(
                    f"{collection}[{index}] has unknown {sorted(item_unknown)} or missing {sorted(missing)} keys."
                )
            normalized: dict[str, str] = {}
            for key, entry in item.items():
                if (
                    not isinstance(entry, str)
                    or not entry.strip()
                    or len(entry.strip()) > 256
                    or any(ord(character) < 32 for character in entry)
                ):
                    raise MappingValidationError(
                        f"{collection}[{index}].{key} must be a safe non-empty string of at most 256 characters."
                    )
                normalized[str(key)] = entry.strip()
            identity = tuple(normalized[key].casefold() for key in identity_keys)
            if identity in seen:
                raise MappingValidationError(f"Duplicate explicit {collection} mapping for {identity}.")
            seen.add(identity)
            items.append(normalized)
        result[collection] = sorted(items, key=lambda item: tuple(item[key].casefold() for key in identity_keys))
    return result


def load_import_mapping_file(path: str | Path) -> dict[str, Any]:
    return validate_import_mapping(_load_mapping(path))


def _load_yaml_mapping(value: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    loaded = yaml.safe_load(Path(value).read_text(encoding="utf-8-sig"))
    if not isinstance(loaded, Mapping):
        raise ValueError("Canonical semantic YAML must contain an object.")
    return loaded


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


@dataclass(frozen=True)
class MappingDecision:
    source_object_id: str
    power_bi_table: str
    power_bi_measure: str
    canonical_metric: str | None
    method: MappingMethod
    canonical_semantic_model: str | None = None
    canonical_entity: str | None = None
    snowflake_logical_table: str | None = None
    snowflake_metric: str | None = None
    required_mappings: tuple[str, ...] = ()
    diagnostics: tuple[ImportDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_object_id": self.source_object_id, "power_bi_table": self.power_bi_table,
            "power_bi_measure": self.power_bi_measure, "canonical_metric": self.canonical_metric,
            "method": self.method.value, "canonical_semantic_model": self.canonical_semantic_model,
            "canonical_entity": self.canonical_entity, "snowflake_logical_table": self.snowflake_logical_table,
            "snowflake_metric": self.snowflake_metric, "required_mappings": list(self.required_mappings),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MappingDecision":
        return cls(
            str(value["source_object_id"]), str(value["power_bi_table"]), str(value["power_bi_measure"]),
            str(value["canonical_metric"]) if value.get("canonical_metric") is not None else None,
            MappingMethod(str(value["method"])),
            str(value["canonical_semantic_model"]) if value.get("canonical_semantic_model") is not None else None,
            str(value["canonical_entity"]) if value.get("canonical_entity") is not None else None,
            str(value["snowflake_logical_table"]) if value.get("snowflake_logical_table") is not None else None,
            str(value["snowflake_metric"]) if value.get("snowflake_metric") is not None else None,
            tuple(str(item) for item in value.get("required_mappings", [])),
            tuple(ImportDiagnostic.from_dict(item) for item in value.get("diagnostics", [])),
        )


def _canonical_indexes(canonical: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[tuple[str, str], str], dict[str, list[str]], dict[str, Mapping[str, Any]]]:
    metrics: dict[str, Mapping[str, Any]] = {}
    configured: dict[tuple[str, str], str] = {}
    normalized: dict[str, list[str]] = {}
    for raw in canonical.get("metrics", []) or []:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
            continue
        name = str(raw["name"])
        metrics[name] = raw
        aliases = {name, str(raw.get("label") or name)}
        for alias in aliases:
            normalized.setdefault(_normalized_name(alias), []).append(name)
        meta = ((raw.get("config") or {}).get("meta") or {})
        power_bi = meta.get("power_bi") or {}
        if isinstance(power_bi, Mapping) and isinstance(power_bi.get("table"), str) and isinstance(power_bi.get("measure"), str):
            key = (str(power_bi["table"]).casefold(), str(power_bi["measure"]).casefold())
            if key in configured:
                raise MappingValidationError(f"Canonical Power BI mapping is ambiguous for {key}.")
            configured[key] = name
    semantic_models = {
        str(item["name"]): item for item in canonical.get("semantic_models", []) or []
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    return metrics, configured, normalized, semantic_models


def resolve_import_mappings(
    inventory: PowerBIModelInventory,
    canonical_yaml: str | Path | Mapping[str, Any],
    explicit_mapping: str | Path | Mapping[str, Any] | None = None,
) -> tuple[MappingDecision, ...]:
    canonical = _load_yaml_mapping(canonical_yaml)
    metrics, configured, normalized, semantic_models = _canonical_indexes(canonical)
    explicit = validate_import_mapping(_load_mapping(explicit_mapping))
    explicit_measures = {
        (item["power_bi_table"].casefold(), item["power_bi_measure"].casefold()): item
        for item in explicit["measures"]
    }
    table_mappings = {item["power_bi_table"].casefold(): item for item in explicit["tables"]}
    inventory_tables = {item.name.casefold(): item for item in inventory.tables}
    inventory_columns = {(item.table.casefold(), item.name.casefold()): item for item in inventory.columns}
    inventory_measures = {(item.table.casefold(), item.name.casefold()): item for item in inventory.measures}
    canonical_fields: dict[str, set[str]] = {}
    canonical_entities: dict[str, set[str]] = {}
    canonical_logical_tables: dict[str, set[str]] = {}
    for model_name, model in semantic_models.items():
        canonical_fields[model_name] = {
            str(item["name"])
            for collection in ("dimensions", "measures")
            for item in model.get(collection, []) or []
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        canonical_entities[model_name] = {
            str(item["name"])
            for item in model.get("entities", []) or []
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        contract = (((model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {})
        canonical_logical_tables[model_name] = {
            str(item["name"])
            for item in contract.get("snowflake_logical_tables", []) or []
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }

    def matching_models(index: Mapping[str, set[str]], name: str) -> list[str]:
        return sorted(
            (model_name for model_name, values in index.items() if name in values),
            key=str.casefold,
        )

    for item in explicit["tables"]:
        table_name = item["power_bi_table"]
        if table_name.casefold() not in inventory_tables:
            raise MappingValidationError(f"Explicit Power BI table does not exist: {table_name}")
        model_name = item.get("canonical_semantic_model")
        if model_name is not None and model_name not in semantic_models:
            raise MappingValidationError(f"Explicit canonical semantic model does not exist: {model_name}")
        entity = item.get("canonical_entity")
        if entity is not None:
            models = [model_name] if model_name is not None else matching_models(canonical_entities, entity)
            if len(models) != 1 or entity not in canonical_entities.get(models[0], set()):
                raise MappingValidationError(f"Explicit canonical entity is missing or ambiguous: {entity}")
        logical_table = item.get("snowflake_logical_table")
        if logical_table is not None:
            models = [model_name] if model_name is not None else matching_models(canonical_logical_tables, logical_table)
            if len(models) != 1 or logical_table not in canonical_logical_tables.get(models[0], set()):
                raise MappingValidationError(
                    f"Explicit Snowflake logical table is missing or ambiguous: {logical_table}"
                )

    for item in explicit["columns"]:
        identity = (item["power_bi_table"].casefold(), item["power_bi_column"].casefold())
        if identity not in inventory_columns:
            raise MappingValidationError(
                f"Explicit Power BI column does not exist: {item['power_bi_table']}[{item['power_bi_column']}]"
            )
        model_name = item.get("canonical_semantic_model")
        if model_name is not None and model_name not in semantic_models:
            raise MappingValidationError(f"Explicit canonical semantic model does not exist: {model_name}")
        field_name = item["canonical_field"]
        models = [model_name] if model_name is not None else matching_models(canonical_fields, field_name)
        if len(models) != 1 or field_name not in canonical_fields.get(models[0], set()):
            raise MappingValidationError(f"Explicit canonical field is missing or ambiguous: {field_name}")

    for item in explicit["measures"]:
        identity = (item["power_bi_table"].casefold(), item["power_bi_measure"].casefold())
        if identity not in inventory_measures:
            raise MappingValidationError(
                f"Explicit Power BI measure does not exist: {item['power_bi_table']}[{item['power_bi_measure']}]"
            )
        if item["canonical_metric"] not in metrics:
            raise MappingValidationError(f"Explicit canonical metric does not exist: {item['canonical_metric']}")
        logical_table = item.get("snowflake_logical_table")
        if logical_table is not None and not matching_models(
            canonical_logical_tables, logical_table
        ):
            raise MappingValidationError(
                f"Explicit Snowflake logical table does not exist: {logical_table}"
            )
    decisions: list[MappingDecision] = []
    for measure in inventory.measures:
        key = (measure.table.casefold(), measure.name.casefold())
        configured_metric = configured.get(key)
        explicit_item = explicit_measures.get(key)
        if configured_metric and explicit_item and explicit_item["canonical_metric"] != configured_metric:
            raise MappingValidationError(
                f"Explicit mapping for {measure.table}[{measure.name}] conflicts with canonical mapping {configured_metric!r}."
            )
        diagnostics: list[ImportDiagnostic] = []
        method: MappingMethod
        metric_name: str | None
        if configured_metric:
            method, metric_name = MappingMethod.CONFIGURED_CANONICAL, configured_metric
        elif explicit_item:
            metric_name = explicit_item["canonical_metric"]
            if metric_name not in metrics:
                raise MappingValidationError(f"Explicit canonical metric does not exist: {metric_name}")
            method = MappingMethod.EXPLICIT
        else:
            matches = sorted(set(normalized.get(_normalized_name(measure.name), [])), key=str.casefold)
            if len(matches) == 1:
                method, metric_name = MappingMethod.EXACT_NORMALIZED, matches[0]
            elif len(matches) > 1:
                method, metric_name = MappingMethod.AMBIGUOUS, None
                diagnostics.append(ImportDiagnostic(
                    "IMPORT_MAPPING_AMBIGUOUS",
                    f"Exact-normalized measure name maps to multiple canonical metrics: {', '.join(matches)}.",
                    source_location=measure.source_location,
                ))
            else:
                method, metric_name = MappingMethod.UNRESOLVED, None
                diagnostics.append(ImportDiagnostic(
                    "IMPORT_MAPPING_MISSING", "No exact configured, explicit, or unique exact-normalized mapping exists.",
                    source_location=measure.source_location,
                ))
        raw_metric = metrics.get(metric_name or "", {})
        meta = ((raw_metric.get("config") or {}).get("meta") or {}) if isinstance(raw_metric, Mapping) else {}
        snowflake = meta.get("snowflake") or {}
        model_name: str | None = None
        entity_name: str | None = None
        logical_table: str | None = None
        if metric_name:
            type_params = raw_metric.get("type_params") or {}
            measure_ref = type_params.get("measure")
            for candidate_name, model in semantic_models.items():
                if any(isinstance(item, Mapping) and item.get("name") == measure_ref for item in model.get("measures", []) or []):
                    model_name = candidate_name
                    primaries = [item for item in model.get("entities", []) or [] if isinstance(item, Mapping) and item.get("type") == "primary"]
                    entity_name = str(primaries[0].get("name")) if len(primaries) == 1 else None
                    break
            logical_table = str(snowflake.get("logical_table")) if isinstance(snowflake, Mapping) and snowflake.get("logical_table") else None
        context_table_names = {
            measure.table.casefold(),
            *(name.casefold() for name in measure.analysis.table_dependencies),
            *(item.table.casefold() for item in measure.analysis.filters),
        }
        context_table_items = [
            table_mappings[name] for name in sorted(context_table_names) if name in table_mappings
        ]
        table_item: Mapping[str, str] | None = None
        for candidate_table_item in context_table_items:
            if table_item is None:
                table_item = candidate_table_item
                continue
            for mapping_key in (
                "canonical_semantic_model",
                "canonical_entity",
                "snowflake_logical_table",
            ):
                first = table_item.get(mapping_key)
                second = candidate_table_item.get(mapping_key)
                if first and second and first != second:
                    raise MappingValidationError(
                        f"Explicit table mappings in the context of {measure.table}[{measure.name}] conflict for {mapping_key}."
                    )
            table_item = {**table_item, **candidate_table_item}
        if table_item:
            if model_name and table_item.get("canonical_semantic_model") and table_item["canonical_semantic_model"] != model_name:
                raise MappingValidationError(f"Explicit table mapping for {measure.table} conflicts with metric source model {model_name}.")
            if entity_name and table_item.get("canonical_entity") and table_item["canonical_entity"] != entity_name:
                raise MappingValidationError(
                    f"Explicit table mapping for {measure.table} conflicts with canonical entity {entity_name}."
                )
            if logical_table and table_item.get("snowflake_logical_table") and table_item["snowflake_logical_table"] != logical_table:
                raise MappingValidationError(
                    f"Explicit table mapping for {measure.table} conflicts with canonical Snowflake logical table {logical_table}."
                )
            model_name = table_item.get("canonical_semantic_model", model_name)
            entity_name = table_item.get("canonical_entity", entity_name)
            logical_table = table_item.get("snowflake_logical_table", logical_table)
        if explicit_item:
            configured_snowflake_metric = (
                str(snowflake.get("metric_name"))
                if isinstance(snowflake, Mapping) and snowflake.get("metric_name")
                else None
            )
            if logical_table and explicit_item.get("snowflake_logical_table") and explicit_item["snowflake_logical_table"] != logical_table:
                raise MappingValidationError(
                    f"Explicit mapping for {measure.table}[{measure.name}] conflicts with canonical Snowflake logical table {logical_table}."
                )
            if (
                configured_snowflake_metric
                and explicit_item.get("snowflake_metric")
                and explicit_item["snowflake_metric"] != configured_snowflake_metric
            ):
                raise MappingValidationError(
                    f"Explicit mapping for {measure.table}[{measure.name}] conflicts with canonical Snowflake metric {configured_snowflake_metric}."
                )
            logical_table = explicit_item.get("snowflake_logical_table", logical_table)
        required = []
        if metric_name is None:
            required.extend(
                (
                    "canonical.metric",
                    "canonical.semantic_model",
                    "canonical.entity",
                    "canonical.snowflake.logical_table",
                    "governance.publication",
                )
            )
        elif method is not MappingMethod.CONFIGURED_CANONICAL:
            required.append("canonical.power_bi")
        if metric_name and not logical_table:
            required.append("canonical.snowflake.logical_table")
        decisions.append(MappingDecision(
            measure.object_id, measure.table, measure.name, metric_name, method, model_name, entity_name,
            logical_table,
            explicit_item.get("snowflake_metric") if explicit_item else (
                str(snowflake.get("metric_name")) if isinstance(snowflake, Mapping) and snowflake.get("metric_name") else metric_name
            ), tuple(required), tuple(diagnostics),
        ))
    return tuple(sorted(decisions, key=lambda item: (item.power_bi_table.casefold(), item.power_bi_measure.casefold())))


@dataclass(frozen=True)
class PowerBIRegenerationComparison:
    semantic_equivalent: bool
    exact_text: bool
    formatting_only: bool
    assumptions: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()
    diagnostics: tuple[ImportDiagnostic, ...] = ()
    mappings_applied: tuple[Mapping[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"semantic_equivalent": self.semantic_equivalent, "exact_text": self.exact_text,
                "formatting_only": self.formatting_only, "assumptions": list(self.assumptions),
                "differences": list(self.differences), "diagnostics": [item.to_dict() for item in self.diagnostics],
                "mappings_applied": [dict(item) for item in self.mappings_applied]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerBIRegenerationComparison":
        return cls(
            semantic_equivalent=bool(value.get("semantic_equivalent")),
            exact_text=bool(value.get("exact_text")),
            formatting_only=bool(value.get("formatting_only")),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            differences=tuple(str(item) for item in value.get("differences", [])),
            diagnostics=tuple(
                ImportDiagnostic.from_dict(item) for item in value.get("diagnostics", []) if isinstance(item, Mapping)
            ),
            mappings_applied=tuple(
                {str(key): str(item[key]) for key in sorted(item)}
                for item in value.get("mappings_applied", [])
                if isinstance(item, Mapping)
            ),
        )


def _mapped_dax_ast(
    ast: Mapping[str, Any],
    table_mappings: Mapping[str, str],
    column_mappings: Mapping[tuple[str, str], tuple[str, str]],
    measure_mappings: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[Mapping[str, str], ...]]:
    result = json.loads(json.dumps(ast, ensure_ascii=False))
    applied: list[Mapping[str, str]] = []
    if result.get("kind") in {
        "COUNT",
        "COLUMN_COUNT",
        "SUM",
        "DISTINCT_COUNT",
        "SCALED_SUM",
        "FILTERED_COUNT",
    }:
        source_table = str(result.get("table", ""))
        mapped_table = table_mappings.get(source_table.casefold(), source_table)
        if mapped_table != source_table:
            applied.append({"kind": "TABLE", "source": source_table, "target": mapped_table})
            result["table"] = mapped_table
        if result.get("kind") in {"COLUMN_COUNT", "SUM", "DISTINCT_COUNT", "SCALED_SUM"}:
            column = str(result.get("column", ""))
            mapped_identity = column_mappings.get((source_table.casefold(), column.casefold()))
            if mapped_identity is None:
                mapped_identity = (mapped_table, column)
            if mapped_identity != (source_table, column):
                applied.append(
                    {
                        "kind": "COLUMN",
                        "source": f"{source_table}[{column}]",
                        "target": f"{mapped_identity[0]}[{mapped_identity[1]}]",
                    }
                )
                result["table"], result["column"] = mapped_identity
        for item in result.get("filters", []) or []:
            table = str(item.get("table", ""))
            column = str(item.get("column", ""))
            mapped_identity = column_mappings.get((table.casefold(), column.casefold()))
            if mapped_identity is None:
                mapped_identity = (table_mappings.get(table.casefold(), table), column)
            if mapped_identity != (table, column):
                applied.append(
                    {
                        "kind": "COLUMN",
                        "source": f"{table}[{column}]",
                        "target": f"{mapped_identity[0]}[{mapped_identity[1]}]",
                    }
                )
                item["table"], item["column"] = mapped_identity
    elif result.get("kind") == "RATIO":
        for field in ("numerator", "denominator"):
            source_name = str(result.get(field, ""))
            mapped_name = measure_mappings.get(source_name.casefold(), source_name)
            if mapped_name != source_name:
                applied.append(
                    {"kind": "MEASURE", "source": source_name, "target": mapped_name}
                )
                result[field] = mapped_name
    unique = {
        (item["kind"], item["source"], item["target"]): item for item in applied
    }
    return result, tuple(unique[key] for key in sorted(unique))


def _ast_equivalent(
    source: Mapping[str, Any], target: Mapping[str, Any], boolean_columns: set[tuple[str, str]],
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    if source.get("kind") != target.get("kind"):
        return False, (), ("pattern",)
    kind = source.get("kind")
    if kind == "COUNT":
        same = str(source.get("table", "")).casefold() == str(target.get("table", "")).casefold()
        return same, (), (() if same else ("source_table",))
    if kind in {"COLUMN_COUNT", "SUM", "DISTINCT_COUNT", "SCALED_SUM"}:
        same_identity = (
            str(source.get("table", "")).casefold()
            == str(target.get("table", "")).casefold()
            and str(source.get("column", "")).casefold()
            == str(target.get("column", "")).casefold()
        )
        if not same_identity:
            return False, (), ("source_column",)
        if kind == "SCALED_SUM":
            try:
                same_divisor = Decimal(str(source.get("divisor"))) == Decimal(
                    str(target.get("divisor"))
                )
            except InvalidOperation:
                same_divisor = False
            return same_divisor, (), (() if same_divisor else ("scale_divisor",))
        return True, (), ()
    if kind == "RATIO":
        same = (
            str(source.get("numerator", "")).casefold()
            == str(target.get("numerator", "")).casefold()
            and str(source.get("denominator", "")).casefold()
            == str(target.get("denominator", "")).casefold()
        )
        return same, (), (() if same else ("metric_references",))
    source_table = str(source.get("table", ""))
    target_table = str(target.get("table", ""))
    if source_table.casefold() != target_table.casefold():
        return False, (), ("source_table",)
    source_filters = source.get("filters") or []
    target_filters = target.get("filters") or []
    if len(source_filters) != len(target_filters):
        return False, (), ("filter_count",)
    source_sorted = sorted(source_filters, key=lambda item: (str(item.get("table", "")).casefold(), str(item.get("column", "")).casefold()))
    target_sorted = sorted(target_filters, key=lambda item: (str(item.get("table", "")).casefold(), str(item.get("column", "")).casefold()))
    assumptions: list[str] = []
    for left, right in zip(source_sorted, target_sorted):
        identity = (str(left.get("table", "")).casefold(), str(left.get("column", "")).casefold())
        if identity != (str(right.get("table", "")).casefold(), str(right.get("column", "")).casefold()):
            return False, (), ("filter_field",)
        left_value, right_value = left.get("value"), right.get("value")
        if type(left_value) is type(right_value) and left_value == right_value:
            continue
        if identity in boolean_columns and {left_value, right_value} == {True, 1}:
            assumptions.append(f"BOOLEAN_ENCODING_EQUIVALENCE:{left['table']}[{left['column']}]:1=TRUE()")
            continue
        if identity in boolean_columns and {left_value, right_value} == {False, 0}:
            assumptions.append(f"BOOLEAN_ENCODING_EQUIVALENCE:{left['table']}[{left['column']}]:0=FALSE()")
            continue
        return False, (), ("filter_value",)
    return True, tuple(assumptions), ()


def compare_regenerated_powerbi(
    source_expression: str,
    regenerated_expression: str,
    *,
    boolean_columns: Iterable[tuple[str, str]] = (),
    table_mappings: Mapping[str, str] | None = None,
    column_mappings: Mapping[tuple[str, str], tuple[str, str]] | None = None,
    measure_mappings: Mapping[str, str] | None = None,
) -> PowerBIRegenerationComparison:
    source = analyze_dax_measure(source_expression)
    target = analyze_dax_measure(regenerated_expression)
    diagnostics = source.diagnostics + target.diagnostics
    if not source.supported or not target.supported or source.ast is None or target.ast is None:
        return PowerBIRegenerationComparison(False, source_expression == regenerated_expression, False,
                                             differences=("unsupported_or_unparsable",), diagnostics=diagnostics)
    mapped_source, mappings_applied = _mapped_dax_ast(
        source.ast,
        {str(key).casefold(): str(value) for key, value in (table_mappings or {}).items()},
        {
            (str(key[0]).casefold(), str(key[1]).casefold()): (str(value[0]), str(value[1]))
            for key, value in (column_mappings or {}).items()
        },
        {
            str(key).casefold(): str(value)
            for key, value in (measure_mappings or {}).items()
        },
    )
    boolean_identities = {(table.casefold(), column.casefold()) for table, column in boolean_columns}
    for (source_table, source_column), (target_table, target_column) in (column_mappings or {}).items():
        if (str(source_table).casefold(), str(source_column).casefold()) in boolean_identities:
            boolean_identities.add((str(target_table).casefold(), str(target_column).casefold()))
    equivalent, assumptions, differences = _ast_equivalent(
        mapped_source, target.ast, boolean_identities
    )
    exact_text = source_expression == regenerated_expression
    formatting_only = equivalent and not exact_text and source.normalized_expression == target.normalized_expression
    return PowerBIRegenerationComparison(
        equivalent,
        exact_text,
        formatting_only,
        assumptions,
        differences,
        diagnostics,
        mappings_applied,
    )


@dataclass(frozen=True)
class ImportMetricCandidate:
    source_object_id: str
    source_object_path: str
    source_location: SourceLocation
    source_table: str
    source_measure: str
    classification: ImportSupportClassification
    recognized_pattern: str | None
    dependencies: tuple[str, ...]
    mapping: MappingDecision
    assumptions: tuple[str, ...]
    unsupported_constructs: tuple[str, ...]
    candidate_ir: Mapping[str, Any] | None
    canonical_draft: Mapping[str, Any] | None
    regenerated_powerbi: str | None
    regenerated_snowflake: Mapping[str, Any] | None
    comparison: PowerBIRegenerationComparison | None
    diagnostics: tuple[ImportDiagnostic, ...] = ()

    @property
    def semantic_hash(self) -> str:
        return _sha256_value(self._payload())

    def _payload(self) -> dict[str, Any]:
        return {
            "source_object_id": self.source_object_id, "source_object_path": self.source_object_path,
            "source_location": self.source_location.to_dict(),
            "source_table": self.source_table, "source_measure": self.source_measure,
            "classification": self.classification.value, "recognized_pattern": self.recognized_pattern,
            "dependencies": list(self.dependencies), "mapping": self.mapping.to_dict(),
            "assumptions": list(self.assumptions), "unsupported_constructs": list(self.unsupported_constructs),
            "candidate_ir": dict(self.candidate_ir) if self.candidate_ir is not None else None,
            "canonical_draft": dict(self.canonical_draft) if self.canonical_draft is not None else None,
            "regenerated_powerbi": self.regenerated_powerbi,
            "regenerated_snowflake": dict(self.regenerated_snowflake) if self.regenerated_snowflake is not None else None,
            "comparison": self.comparison.to_dict() if self.comparison is not None else None,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "semantic_hash": self.semantic_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImportMetricCandidate":
        comparison = value.get("comparison")
        candidate_ir = value.get("candidate_ir")
        canonical_draft = value.get("canonical_draft")
        regenerated_snowflake = value.get("regenerated_snowflake")
        result = cls(
            source_object_id=str(value["source_object_id"]),
            source_object_path=str(value["source_object_path"]),
            source_location=SourceLocation.from_dict(value["source_location"]),
            source_table=str(value["source_table"]),
            source_measure=str(value["source_measure"]),
            classification=ImportSupportClassification(str(value["classification"])),
            recognized_pattern=str(value["recognized_pattern"]) if value.get("recognized_pattern") is not None else None,
            dependencies=tuple(str(item) for item in value.get("dependencies", [])),
            mapping=MappingDecision.from_dict(value["mapping"]),
            assumptions=tuple(str(item) for item in value.get("assumptions", [])),
            unsupported_constructs=tuple(str(item) for item in value.get("unsupported_constructs", [])),
            candidate_ir=dict(candidate_ir) if isinstance(candidate_ir, Mapping) else None,
            canonical_draft=dict(canonical_draft) if isinstance(canonical_draft, Mapping) else None,
            regenerated_powerbi=(str(value["regenerated_powerbi"]) if value.get("regenerated_powerbi") is not None else None),
            regenerated_snowflake=(dict(regenerated_snowflake) if isinstance(regenerated_snowflake, Mapping) else None),
            comparison=(PowerBIRegenerationComparison.from_dict(comparison) if isinstance(comparison, Mapping) else None),
            diagnostics=tuple(
                ImportDiagnostic.from_dict(item) for item in value.get("diagnostics", []) if isinstance(item, Mapping)
            ),
        )
        supplied_hash = value.get("semantic_hash")
        if supplied_hash is not None and supplied_hash != result.semantic_hash:
            raise ValueError("Import metric candidate semantic_hash does not match its content.")
        return result


def _physical_table_for_model(model: Mapping[str, Any], logical_table: str | None) -> str | None:
    contract = (((model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {})
    tables = [item for item in contract.get("snowflake_logical_tables", []) or [] if isinstance(item, Mapping)]
    if logical_table:
        tables = [item for item in tables if item.get("name") == logical_table]
    primaries = [item for item in model.get("entities", []) or [] if isinstance(item, Mapping) and item.get("type") == "primary"]
    if len(primaries) == 1 and primaries[0].get("expr"):
        matching = [item for item in tables if item.get("primary_key") == primaries[0].get("expr")]
        if matching:
            tables = matching
    return str(tables[0].get("base_table")) if len(tables) == 1 and tables[0].get("base_table") else None


def _metric_ir_payload(metric_ir: SemanticMetricIR) -> dict[str, Any]:
    payload = {
        "canonical_metric": metric_ir.canonical_name,
        "pattern": metric_ir.pattern.value if metric_ir.pattern else None,
        "public": metric_ir.public,
        "source_semantic_model": metric_ir.source_semantic_model,
        "source_entity": metric_ir.source_entity,
        "source_logical_table": metric_ir.source_logical_table,
        "source_physical_table": metric_ir.source_physical_table,
        "source_field": metric_ir.source_field,
        "scale_divisor": metric_ir.scale_divisor,
        "filters": [
            {
                "field": item.field,
                "operator": item.operator.value,
                "value": item.value,
                **({"keep_filters": True} if item.keep_filters else {}),
            }
            for item in metric_ir.filters
        ],
        "numerator": metric_ir.numerator,
        "denominator": metric_ir.denominator,
        "trace_id": metric_ir.trace_id,
        "canonical_source": metric_ir.source.file,
    }
    if metric_ir.source_fields:
        payload["source_fields"] = list(metric_ir.source_fields)
    if metric_ir.metric_references:
        payload["metric_references"] = list(metric_ir.metric_references)
    return payload


def _canonical_candidate(
    metric_name: str,
    canonical: Mapping[str, Any],
    typed_ir_index: Mapping[str, SemanticMetricIR],
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    metrics = {
        str(item["name"]): item
        for item in canonical.get("metrics", []) or []
        if isinstance(item, Mapping) and item.get("name")
    }
    metric = metrics.get(metric_name)
    metric_ir = typed_ir_index.get(metric_name)
    if metric is None or metric_ir is None:
        return None, None, None, None
    dax_result = generate_dax_definition(metric_ir, typed_ir_index)
    snowflake_result = generate_snowflake_definition(metric_ir, typed_ir_index)
    if (
        dax_result.support is not SupportClassification.SUPPORTED_PATTERN
        or dax_result.definition is None
        or snowflake_result.support is not SupportClassification.SUPPORTED_PATTERN
        or snowflake_result.definition is None
    ):
        return None, None, None, None
    return (
        _metric_ir_payload(metric_ir),
        dax_result.definition,
        snowflake_result.definition,
        dict(metric),
    )


def _draft_identifier(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not result:
        result = "imported_metric"
    if result[0].isdigit():
        result = "imported_" + result
    return result


def _unmapped_metric_draft(
    measure: PowerBIMeasure,
    decisions: Mapping[str, MappingDecision],
    typed_ir_index: Mapping[str, SemanticMetricIR],
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    """Create a deterministic private/undecided draft from a supported DAX AST."""

    analysis = measure.analysis
    if not analysis.supported or analysis.ast is None or analysis.pattern is None:
        raise ValueError("An unmapped metric draft requires a supported DAX AST.")
    metric_name = _draft_identifier(measure.name)
    required_mappings = (
        "canonical.semantic_model",
        "canonical.entity",
        "canonical.snowflake.logical_table",
    )
    semantic_measure: dict[str, Any] | None = None
    dependency_drafts: list[dict[str, Any]] = []
    metric_type = "simple"
    pattern = MetricPattern(analysis.pattern)
    aggregation = {
        MetricPattern.RATIO: Aggregation.RATIO,
        MetricPattern.SUM: Aggregation.SUM,
        MetricPattern.SCALED_SUM: Aggregation.SUM,
        MetricPattern.AVERAGE: Aggregation.AVERAGE,
        MetricPattern.MIN: Aggregation.MIN,
        MetricPattern.MAX: Aggregation.MAX,
        MetricPattern.DISTINCT_COUNT: Aggregation.DISTINCT_COUNT,
        MetricPattern.SUM_ADDITION: Aggregation.DERIVED,
        MetricPattern.METRIC_ADDITION: Aggregation.DERIVED,
        MetricPattern.SCALED_METRIC: Aggregation.DERIVED,
    }.get(pattern, Aggregation.COUNT)
    filters = tuple(
        FilterPredicate(
            item.column,
            FilterOperator.EQ,
            item.value,
            keep_filters=item.keep_filters,
        )
        for item in analysis.filters
    )
    numerator: str | None = None
    denominator: str | None = None
    metric_references: tuple[str, ...] = ()
    scale_divisor: str | None = None
    source_field: str | None = None
    source_fields: tuple[str, ...] = ()
    generation_index = dict(typed_ir_index)

    def canonical_reference(source_name: str) -> str:
        matches = [
            item.canonical_metric
            for item in decisions.values()
            if item.power_bi_measure.casefold() == source_name.casefold()
            and item.canonical_metric is not None
        ]
        return matches[0] if len(set(matches)) == 1 else _draft_identifier(source_name)

    def ensure_reference(reference: str, source_name: str) -> None:
        if reference in generation_index:
            return
        generation_index[reference] = SemanticMetricIR(
            canonical_name=reference,
            label=source_name,
            description="Power BI import dependency evidence.",
            public=False,
            source=CanonicalSourceLocation(
                CANONICAL_SOURCE, f"powerbi_import.dependencies[{reference}]"
            ),
            trace_id=f"powerbi-import:{measure.object_id}:dependency:{reference}",
            pattern=MetricPattern.COUNT,
            aggregation=Aggregation.COUNT,
            source_semantic_model=None,
            source_entity=None,
            source_logical_table="__REQUIRES_MAPPING__",
            source_physical_table=str(analysis.ast.get("table") or "__unmapped__"),
            source_field="*",
            power_bi=PowerBIMapping(measure=source_name),
            snowflake=SnowflakeMapping(
                logical_table="__REQUIRES_MAPPING__",
                metric_name=reference,
            ),
            support=SupportClassification.SUPPORTED_PATTERN,
        )

    if analysis.pattern == "COUNT":
        semantic_measure = {"name": metric_name, "agg": "sum", "expr": 1}
        source_field = "*"
        type_params = {"measure": metric_name}
    elif analysis.pattern in {
        "SUM",
        "AVERAGE",
        "MIN",
        "MAX",
        "COLUMN_COUNT",
        "DISTINCT_COUNT",
        "SCALED_SUM",
    }:
        source_field = str(analysis.ast["column"])
        aggregate = {
            "SUM": "sum",
            "AVERAGE": "average",
            "MIN": "min",
            "MAX": "max",
            "COLUMN_COUNT": "count",
            "DISTINCT_COUNT": "count_distinct",
            "SCALED_SUM": "sum",
        }[analysis.pattern]
        expression = source_field
        if analysis.pattern == "SCALED_SUM":
            scale_divisor = format(
                Decimal(str(analysis.ast["divisor"])).normalize(),
                "f",
            )
            expression = f"{source_field} / {scale_divisor}"
        semantic_measure = {
            "name": metric_name,
            "agg": aggregate,
            "expr": expression,
        }
        type_params = {"measure": metric_name}
    elif analysis.pattern == "FILTERED_COUNT":
        semantic_measure = {
            "name": metric_name,
            "agg": "sum_boolean",
            "expr": render_canonical_filter_expression(filters),
        }
        source_field = "*"
        type_params = {"measure": metric_name}
    elif analysis.pattern == "SUM_ADDITION":
        metric_type = "derived"
        source_fields = tuple(
            str(item["column"])
            for item in analysis.ast["fields"]
        )
        dependency_names: list[str] = []
        for index, field_name in enumerate(source_fields, start=1):
            dependency_name = f"{metric_name}__sum_{index}"
            dependency_names.append(dependency_name)
            dependency_drafts.append(
                {
                    "semantic_measure": {
                        "name": dependency_name,
                        "agg": "sum",
                        "expr": field_name,
                    },
                    "metric": {
                        "name": dependency_name,
                        "type": "simple",
                        "type_params": {"measure": dependency_name},
                        "private": True,
                    },
                }
            )
        type_params = {
            "expr": " + ".join(dependency_names),
            "metrics": dependency_names,
        }
    elif analysis.pattern == "RATIO":
        metric_type = "ratio"
        numerator_source = str(analysis.ast["numerator"])
        denominator_source = str(analysis.ast["denominator"])

        numerator = canonical_reference(numerator_source)
        denominator = canonical_reference(denominator_source)

        ensure_reference(numerator, numerator_source)
        ensure_reference(denominator, denominator_source)
        type_params = {"numerator": numerator, "denominator": denominator}
    elif analysis.pattern in {"METRIC_ADDITION", "SCALED_METRIC"}:
        metric_type = "derived"
        source_references = tuple(
            str(item)
            for item in (
                analysis.ast["references"]
                if analysis.pattern == "METRIC_ADDITION"
                else (analysis.ast["reference"],)
            )
        )
        metric_references = tuple(
            canonical_reference(source_name)
            for source_name in source_references
        )
        for reference, source_name in zip(metric_references, source_references):
            ensure_reference(reference, source_name)
        if analysis.pattern == "SCALED_METRIC":
            scale_divisor = format(
                Decimal(str(analysis.ast["divisor"])).normalize(),
                "f",
            )
            expression = f"{metric_references[0]} / {scale_divisor}"
        else:
            expression = " + ".join(metric_references)
        type_params = {
            "expr": expression,
            "metrics": list(metric_references),
        }
    else:
        raise ValueError(f"Unsupported draft pattern: {analysis.pattern}")

    metric_ir = SemanticMetricIR(
        canonical_name=metric_name,
        label=measure.name,
        description=measure.description,
        public=False,
        source=CanonicalSourceLocation(
            CANONICAL_SOURCE, f"powerbi_import.measures[{measure.object_id}]"
        ),
        trace_id=f"powerbi-import:{measure.object_id}",
        pattern=pattern,
        aggregation=aggregation,
        source_semantic_model=None,
        source_entity=None,
        source_logical_table=None,
        source_physical_table=str(analysis.ast.get("table") or "") or None,
        source_field=source_field,
        source_fields=source_fields,
        scale_divisor=scale_divisor,
        filters=filters,
        numerator=numerator,
        denominator=denominator,
        metric_references=metric_references,
        power_bi=PowerBIMapping(
            table=measure.table,
            measure=measure.name,
            format_string=measure.format_string,
            display_folder=measure.display_folder,
        ),
        snowflake=SnowflakeMapping(
            logical_table=(
                "__REQUIRES_MAPPING__"
                if pattern
                in {
                    MetricPattern.RATIO,
                    MetricPattern.METRIC_ADDITION,
                    MetricPattern.SCALED_METRIC,
                }
                else None
            ),
            metric_name=metric_name,
        ),
        support=SupportClassification.SUPPORTED_PATTERN,
    )
    generation_index[metric_name] = metric_ir
    dax_result = generate_dax_definition(metric_ir, generation_index)
    snowflake_result = generate_snowflake_definition(metric_ir, generation_index)
    if (
        dax_result.support is not SupportClassification.SUPPORTED_PATTERN
        or dax_result.definition is None
        or snowflake_result.support is not SupportClassification.SUPPORTED_PATTERN
        or snowflake_result.definition is None
    ):
        raise ValueError("Typed IR could not generate deterministic import draft targets.")

    semantic_contract_meta: dict[str, Any] = {
        "public": False,
        "publication_decision": "UNDECIDED",
        "version": 1,
    }
    if (
        len(filters) == 1
        and filters[0].keep_filters
        and isinstance(filters[0].value, bool)
    ):
        semantic_contract_meta[
            FILTER_CONTEXT_BEHAVIOR_META_KEY
        ] = FILTER_CONTEXT_INTERSECT_EXISTING

    metric = {
        "name": metric_name,
        "label": measure.name,
        "description": measure.description,
        "type": metric_type,
        "type_params": type_params,
        "config": {
            "meta": {
                "semantic_contract": semantic_contract_meta,
                "power_bi": {
                    "table": measure.table,
                    "measure": measure.name,
                    "format_string": measure.format_string,
                    "display_folder": measure.display_folder,
                },
                "snowflake": {
                    "logical_table": None,
                    "metric_name": metric_name,
                    "synonyms": [],
                },
            }
        },
    }
    canonical_draft = {
        "status": "MANUAL_REVIEW_REQUIRED",
        "authority": "POWER_BI_EVIDENCE_ONLY",
        "publication_decision": "UNDECIDED_PRIVATE_DEFAULT",
        "semantic_model": None,
        "entity": None,
        "semantic_measure": semantic_measure,
        "metric": metric,
        "required_mappings": list(required_mappings),
        "source_evidence": {
            "power_bi_object": f"{measure.table}[{measure.name}]",
            "expression_sha256": measure.expression_hash,
        },
    }
    if dependency_drafts:
        canonical_draft["dependency_drafts"] = dependency_drafts
    snowflake = {
        **snowflake_result.definition,
        "logical_table": None,
        "status": "DRAFT_REQUIRES_MAPPING",
    }
    candidate_ir = {
        **_metric_ir_payload(metric_ir),
        "publication_decision": "UNDECIDED_PRIVATE_DEFAULT",
        "required_mappings": list(required_mappings),
    }
    canonical_draft["required_mappings"] = list(required_mappings)
    return candidate_ir, dax_result.definition, snowflake, canonical_draft


def _complete_mapped_canonical_draft(
    canonical_metric: Mapping[str, Any],
    measure: PowerBIMeasure,
    mapping: MappingDecision,
    *,
    configured_exact: bool,
) -> dict[str, Any]:
    metric = json.loads(json.dumps(canonical_metric, ensure_ascii=False))
    config = metric.setdefault("config", {})
    meta = config.setdefault("meta", {})
    if not configured_exact:
        meta["power_bi"] = {
            "table": measure.table,
            "measure": measure.name,
            "format_string": measure.format_string,
            "display_folder": measure.display_folder,
        }
        snowflake = dict(meta.get("snowflake") or {})
        snowflake.update(
            {
                "logical_table": mapping.snowflake_logical_table,
                "metric_name": mapping.snowflake_metric or mapping.canonical_metric,
                "synonyms": list(snowflake.get("synonyms") or []),
            }
        )
        meta["snowflake"] = snowflake
    contract = meta.get("semantic_contract") or {}
    publication = "PUBLIC_UNCHANGED" if contract.get("public") else "PRIVATE_UNCHANGED"
    return {
        "status": "RECONCILIATION_NO_OP" if configured_exact else "MANUAL_REVIEW_REQUIRED",
        "authority": "CANONICAL_DBT",
        "publication_decision": publication,
        "metric": metric,
        "required_mappings": list(mapping.required_mappings),
    }


def _explicit_context_aliases(
    canonical: Mapping[str, Any],
    explicit_mapping: str | Path | Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[tuple[str, str], tuple[str, str]]]:
    explicit = validate_import_mapping(_load_mapping(explicit_mapping))
    semantic_models = {
        str(item["name"]): item
        for item in canonical.get("semantic_models", []) or []
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }

    def resolve_model(item: Mapping[str, str]) -> tuple[str, Mapping[str, Any]] | None:
        configured = item.get("canonical_semantic_model")
        if configured is not None:
            model = semantic_models.get(configured)
            return (configured, model) if model is not None else None
        matches: list[tuple[str, Mapping[str, Any]]] = []
        for model_name, model in semantic_models.items():
            entities = {
                str(entry.get("name"))
                for entry in model.get("entities", []) or []
                if isinstance(entry, Mapping)
            }
            contract = (((model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {})
            logical_tables = {
                str(entry.get("name"))
                for entry in contract.get("snowflake_logical_tables", []) or []
                if isinstance(entry, Mapping)
            }
            if item.get("canonical_entity") and item["canonical_entity"] not in entities:
                continue
            if item.get("snowflake_logical_table") and item["snowflake_logical_table"] not in logical_tables:
                continue
            if item.get("canonical_entity") or item.get("snowflake_logical_table"):
                matches.append((model_name, model))
        return matches[0] if len(matches) == 1 else None

    table_aliases: dict[str, str] = {}
    table_models: dict[str, Mapping[str, Any]] = {}
    for item in explicit["tables"]:
        resolved = resolve_model(item)
        if resolved is None:
            continue
        _, model = resolved
        physical = _physical_table_for_model(model, item.get("snowflake_logical_table"))
        if physical is not None:
            table_aliases[item["power_bi_table"].casefold()] = physical
            table_models[item["power_bi_table"].casefold()] = model

    column_aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for item in explicit["columns"]:
        table_key = item["power_bi_table"].casefold()
        model = table_models.get(table_key)
        if model is None:
            resolved = resolve_model(item)
            model = resolved[1] if resolved is not None else None
        physical = table_aliases.get(table_key)
        if physical is None and model is not None:
            physical = _physical_table_for_model(model, None)
        if physical is not None:
            column_aliases[(table_key, item["power_bi_column"].casefold())] = (
                physical,
                item["canonical_field"],
            )
    return table_aliases, column_aliases


_MANUAL_DAX_CODES = frozenset(
    {
        "DAX_NESTED_CALCULATE",
        "DAX_FILTER_CONTEXT_MODIFIER",
        "DAX_FILTER_STATE_DEPENDENCY",
        "DAX_VISUAL_SCOPE_DEPENDENCY",
        "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY",
        "DAX_TIME_INTELLIGENCE",
        "DAX_DYNAMIC_FORMAT_STRING",
        "DAX_CALCULATION_GROUP",
        "DAX_CONTEXT_TRANSITION",
        "DAX_AMBIGUOUS_MEASURE_REFERENCE",
        "DAX_MEASURE_DEPENDENCY_CYCLE",
        "DAX_UNSAFE_TRANSITIVE_DEPENDENCY",
    }
)


def build_import_metric_candidates(
    inventory: PowerBIModelInventory,
    canonical_yaml: str | Path | Mapping[str, Any],
    semantic_manifest: str | Path | Mapping[str, Any] | None = None,
    explicit_mapping: str | Path | Mapping[str, Any] | None = None,
) -> tuple[ImportMetricCandidate, ...]:
    """Build deterministic import candidates without creating or applying proposals.

    The canonical YAML remains authoritative. A supplied dbt semantic manifest
    is consumed by the shared typed-IR builder; otherwise the canonical-only
    shared entry point is used. Neither path maintains a second interpretation
    or target generator for mapped canonical metrics.
    """
    canonical = _load_yaml_mapping(canonical_yaml)
    if semantic_manifest is None:
        typed_ir_index = build_canonical_metric_ir_index(
            canonical,
            canonical_source=CANONICAL_SOURCE,
        )
    else:
        manifest = _load_yaml_mapping(semantic_manifest)
        typed_ir_index = build_metric_ir_index(
            manifest,
            canonical,
            canonical_source=CANONICAL_SOURCE,
        )
    decisions_tuple = resolve_import_mappings(inventory, canonical, explicit_mapping)
    decisions = {item.source_object_id: item for item in decisions_tuple}
    table_aliases, column_aliases = _explicit_context_aliases(canonical, explicit_mapping)
    measure_alias_candidates: dict[str, set[str]] = {}
    for decision in decisions_tuple:
        if (
            decision.canonical_metric is None
            or decision.method not in {MappingMethod.CONFIGURED_CANONICAL, MappingMethod.EXPLICIT}
        ):
            continue
        referenced_ir = typed_ir_index.get(decision.canonical_metric)
        if referenced_ir is None:
            continue
        target_name = referenced_ir.power_bi.measure or referenced_ir.label
        measure_alias_candidates.setdefault(
            decision.power_bi_measure.casefold(), set()
        ).add(target_name)
    measure_aliases = {
        source: next(iter(targets))
        for source, targets in measure_alias_candidates.items()
        if len(targets) == 1
    }
    boolean_columns = {(item.table, item.name) for item in inventory.columns if (item.data_type or "").casefold() == "boolean"}
    candidates: list[ImportMetricCandidate] = []
    for measure in inventory.measures:
        mapping = decisions[measure.object_id]
        diagnostics = list(measure.analysis.diagnostics) + list(mapping.diagnostics)
        unsupported = tuple(sorted({item.code for item in measure.analysis.diagnostics}))
        candidate_ir: dict[str, Any] | None = None
        regenerated_dax: str | None = None
        regenerated_snowflake: dict[str, Any] | None = None
        canonical_draft: dict[str, Any] | None = None
        comparison: PowerBIRegenerationComparison | None = None
        assumptions: tuple[str, ...] = ()
        if not measure.analysis.supported:
            classification = (
                ImportSupportClassification.MANUAL_REVIEW_REQUIRED
                if any(item.code in _MANUAL_DAX_CODES for item in measure.analysis.diagnostics)
                else ImportSupportClassification.UNSUPPORTED
            )
        elif mapping.canonical_metric is None:
            candidate_ir, regenerated_dax, regenerated_snowflake, canonical_draft = _unmapped_metric_draft(
                measure, decisions, typed_ir_index
            )
            comparison = compare_regenerated_powerbi(
                measure.expression,
                regenerated_dax,
                boolean_columns=boolean_columns,
                table_mappings=table_aliases,
                column_mappings=column_aliases,
                measure_mappings=measure_aliases,
            )
            assumptions = comparison.assumptions
            diagnostics.append(
                ImportDiagnostic(
                    "IMPORT_NEW_METRIC_DRAFT",
                    "A private/undecided canonical draft was created from Power BI evidence; mappings and publication require human governance.",
                    severity="WARNING",
                    source_location=measure.source_location,
                )
            )
            classification = ImportSupportClassification.MANUAL_REVIEW_REQUIRED
        else:
            candidate_ir, regenerated_dax, regenerated_snowflake, canonical_draft = _canonical_candidate(
                mapping.canonical_metric, canonical, typed_ir_index
            )
            if candidate_ir is None or regenerated_dax is None:
                diagnostics.append(ImportDiagnostic("CANONICAL_IR_UNRESOLVED", "Mapped canonical metric cannot be represented by the M5 IR."))
                classification = ImportSupportClassification.MANUAL_REVIEW_REQUIRED
            else:
                comparison = compare_regenerated_powerbi(
                    measure.expression,
                    regenerated_dax,
                    boolean_columns=boolean_columns,
                    table_mappings=table_aliases,
                    column_mappings=column_aliases,
                    measure_mappings=measure_aliases,
                )
                assumptions = comparison.assumptions
                if not comparison.semantic_equivalent:
                    diagnostics.extend(comparison.diagnostics)
                    diagnostics.append(ImportDiagnostic("POWER_BI_REGENERATION_NOT_EQUIVALENT", "Regenerated DAX is not AST-equivalent to the source."))
                    classification = ImportSupportClassification.MANUAL_REVIEW_REQUIRED
                elif assumptions:
                    classification = ImportSupportClassification.SUPPORTED_WITH_ASSUMPTIONS
                elif mapping.method is MappingMethod.CONFIGURED_CANONICAL:
                    classification = ImportSupportClassification.SUPPORTED_EXACT
                else:
                    classification = ImportSupportClassification.SUPPORTED_WITH_MAPPING
                canonical_draft = _complete_mapped_canonical_draft(
                    canonical_draft,
                    measure,
                    mapping,
                    configured_exact=mapping.method is MappingMethod.CONFIGURED_CANONICAL,
                )
        candidates.append(ImportMetricCandidate(
            measure.object_id,
            f"{measure.source_location.file}/measure/{measure.object_id}",
            measure.source_location,
            measure.table,
            measure.name,
            classification, measure.analysis.pattern, measure.analysis.measure_dependencies, mapping,
            assumptions, unsupported, candidate_ir, canonical_draft, regenerated_dax, regenerated_snowflake,
            comparison, tuple(diagnostics),
        ))
    by_name = {item.source_measure.casefold(): item for item in candidates}
    changed = True
    while changed:
        changed = False
        next_candidates: list[ImportMetricCandidate] = []
        for candidate in candidates:
            unsafe_dependencies = sorted(
                dependency
                for dependency in candidate.dependencies
                if dependency.casefold() in by_name
                and by_name[dependency.casefold()].classification
                not in {
                    ImportSupportClassification.SUPPORTED_EXACT,
                    ImportSupportClassification.SUPPORTED_WITH_MAPPING,
                    ImportSupportClassification.SUPPORTED_WITH_ASSUMPTIONS,
                }
            )
            already_flagged = any(
                item.code == "DAX_UNSAFE_TRANSITIVE_DEPENDENCY"
                for item in candidate.diagnostics
            )
            if unsafe_dependencies and not already_flagged:
                diagnostic = ImportDiagnostic(
                    "DAX_UNSAFE_TRANSITIVE_DEPENDENCY",
                    "Measure depends on unsupported or unresolved measure(s): "
                    + ", ".join(unsafe_dependencies)
                    + ".",
                    source_location=candidate.source_location,
                )
                candidate = replace(
                    candidate,
                    classification=ImportSupportClassification.MANUAL_REVIEW_REQUIRED,
                    unsupported_constructs=tuple(
                        sorted(
                            {
                                *candidate.unsupported_constructs,
                                "DAX_UNSAFE_TRANSITIVE_DEPENDENCY",
                            }
                        )
                    ),
                    candidate_ir=None,
                    canonical_draft=None,
                    regenerated_powerbi=None,
                    regenerated_snowflake=None,
                    diagnostics=candidate.diagnostics + (diagnostic,),
                )
                changed = True
            next_candidates.append(candidate)
        candidates = next_candidates
        by_name = {item.source_measure.casefold(): item for item in candidates}
    return tuple(sorted(candidates, key=lambda item: (item.source_table.casefold(), item.source_measure.casefold())))


@dataclass(frozen=True)
class RelationshipFinding:
    finding_type: RelationshipFindingType
    code: str
    message: str
    relationship_id: str | None = None
    canonical_relationship: str | None = None
    informational: bool = False
    ambiguous_filter_path: bool = False
    property_differences: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"finding_type": self.finding_type.value, "code": self.code, "message": self.message,
                "relationship_id": self.relationship_id, "canonical_relationship": self.canonical_relationship,
                "informational": self.informational, "ambiguous_filter_path": self.ambiguous_filter_path,
                "property_differences": [dict(item) for item in self.property_differences]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RelationshipFinding":
        return cls(
            finding_type=RelationshipFindingType(str(value["finding_type"])),
            code=str(value["code"]),
            message=str(value["message"]),
            relationship_id=str(value["relationship_id"]) if value.get("relationship_id") is not None else None,
            canonical_relationship=(
                str(value["canonical_relationship"]) if value.get("canonical_relationship") is not None else None
            ),
            informational=bool(value.get("informational")),
            ambiguous_filter_path=bool(value.get("ambiguous_filter_path")),
            property_differences=tuple(
                dict(item) for item in value.get("property_differences", []) if isinstance(item, Mapping)
            ),
        )


def _canonical_relationships(value: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, Path)):
        value = _load_yaml_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    if "relationships" in value and isinstance(value["relationships"], list):
        return [item for item in value["relationships"] if isinstance(item, Mapping)]
    for model in value.get("semantic_models", []) or []:
        if not isinstance(model, Mapping):
            continue
        contract = (((model.get("config") or {}).get("meta") or {}).get("semantic_contract") or {})
        if isinstance(contract.get("relationships"), list):
            return [item for item in contract["relationships"] if isinstance(item, Mapping)]
    return []


def _has_active_path(relationships: Sequence[PowerBIRelationship], source: str, target: str) -> bool:
    """Return whether a coherent active filter path connects either endpoint.

    TMDL's conventional many-to-one relationship filters from the ``to``
    (one) side toward the ``from`` (many) side.  Bidirectional relationships
    contribute both edges.  Checking reachability in each endpoint direction
    keeps this connectivity question symmetric without treating every
    individual relationship as undirected; a chain whose arrows do not line
    up therefore no longer produces a false ambiguous-path finding.
    """

    graph: dict[str, set[str]] = {}
    for relationship in relationships:
        if not relationship.is_active:
            continue
        from_table = relationship.from_table.casefold()
        to_table = relationship.to_table.casefold()
        direction = re.sub(r"[^a-z]", "", relationship.cross_filter_direction.casefold())
        if direction in {"both", "bothdirection", "bothdirections", "bidirectional"}:
            edges = ((from_table, to_table), (to_table, from_table))
        elif (
            relationship.from_cardinality.casefold() == "one"
            and relationship.to_cardinality.casefold() != "one"
        ):
            edges = ((from_table, to_table),)
        else:
            # The normal TMDL shape is from=many, to=one.  For one-to-one
            # and many-to-many relationships, the explicit from/to ordering
            # remains the only deterministic evidence for oneDirection.
            edges = ((to_table, from_table),)
        for filter_source, filter_target in edges:
            graph.setdefault(filter_source, set()).add(filter_target)

    def reachable(path_source: str, path_target: str) -> bool:
        pending = [path_source]
        visited: set[str] = set()
        while pending:
            node = pending.pop()
            if node == path_target:
                return True
            if node in visited:
                continue
            visited.add(node)
            pending.extend(graph.get(node, set()) - visited)
        return False

    normalized_source = source.casefold()
    normalized_target = target.casefold()
    return reachable(normalized_source, normalized_target) or reachable(normalized_target, normalized_source)


def analyze_import_relationships(
    inventory: PowerBIModelInventory,
    canonical_yaml_or_relationships: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> tuple[RelationshipFinding, ...]:
    """Compare relationships as evidence only; never generate operations."""

    canonical = _canonical_relationships(canonical_yaml_or_relationships)
    pbi_by_signature = {item.signature: item for item in inventory.relationships}
    expected_signatures: set[tuple[str, str, str, str]] = set()
    findings: list[RelationshipFinding] = []
    for item in canonical:
        values = tuple(str(item.get(key, "")) for key in ("from_table", "from_column", "to_table", "to_column"))
        signature = tuple(value.casefold() for value in values)
        expected_signatures.add(signature)  # type: ignore[arg-type]
        name = str(item.get("name") or "_".join(values))
        match = pbi_by_signature.get(signature)  # type: ignore[arg-type]
        if match is not None:
            raw_expected_active = item.get("is_active", item.get("active", True))
            expected_properties = {
                "is_active": not (
                    raw_expected_active is False
                    or str(raw_expected_active).casefold() == "false"
                ),
                "from_cardinality": str(item.get("from_cardinality", "many")),
                "to_cardinality": str(item.get("to_cardinality", "one")),
                "cross_filter_direction": str(item.get("cross_filter_direction", "oneDirection")),
            }
            actual_properties = {
                "is_active": match.is_active,
                "from_cardinality": match.from_cardinality,
                "to_cardinality": match.to_cardinality,
                "cross_filter_direction": match.cross_filter_direction,
            }
            property_differences = tuple(
                {
                    "property": property_name,
                    "canonical": expected_properties[property_name],
                    "power_bi": actual_properties[property_name],
                }
                for property_name in sorted(expected_properties)
                if (
                    expected_properties[property_name]
                    if isinstance(expected_properties[property_name], bool)
                    else str(expected_properties[property_name]).casefold()
                )
                != (
                    actual_properties[property_name]
                    if isinstance(actual_properties[property_name], bool)
                    else str(actual_properties[property_name]).casefold()
                )
            )
            if property_differences:
                findings.append(RelationshipFinding(
                    RelationshipFindingType.MANUAL_REVIEW_REQUIRED,
                    "PBI_RELATIONSHIP_PROPERTY_MISMATCH",
                    f"Canonical relationship {name} has Power BI active/cardinality/filter-direction drift.",
                    match.object_id,
                    name,
                    property_differences=property_differences,
                ))
            else:
                findings.append(RelationshipFinding(
                    RelationshipFindingType.EXACT_MATCH, "PBI_RELATIONSHIP_EXACT_MATCH",
                    f"Canonical relationship {name} matches the Power BI target.", match.object_id, name,
                ))
            continue
        ambiguous = _has_active_path(inventory.relationships, values[0], values[2])
        code = (
            "PBI_RELATIONSHIP_DRIFT_FCT_RESULT_DISTANCE_ID"
            if tuple(value.casefold() for value in values) == ("fct_result", "distance_id", "dim_distance", "distance_id")
            else "PBI_RELATIONSHIP_MISSING_CANONICAL"
        )
        message = f"Canonical relationship {name} is missing from Power BI."
        if ambiguous:
            message += " An active indirect filter path already connects the tables; automatic addition would be ambiguous."
        findings.append(RelationshipFinding(
            RelationshipFindingType.MISSING_CANONICAL_RELATIONSHIP, code, message,
            canonical_relationship=name, ambiguous_filter_path=ambiguous,
        ))
    for relationship in inventory.relationships:
        if relationship.signature not in expected_signatures:
            findings.append(RelationshipFinding(
                RelationshipFindingType.EXTRA_TARGET_RELATIONSHIP, "PBI_RELATIONSHIP_EXTRA_TARGET",
                f"Power BI relationship {relationship.name} has no canonical relationship entry.",
                relationship.object_id, informational=True,
            ))
        if not relationship.is_active:
            endpoint_text = {
                relationship.from_table.casefold(), relationship.from_column.casefold(),
                relationship.to_table.casefold(), relationship.to_column.casefold(),
            }
            dependents = [
                measure for measure in inventory.measures
                if any(item.code == "DAX_INACTIVE_RELATIONSHIP_DEPENDENCY" for item in measure.analysis.diagnostics)
                and any(value in measure.expression.casefold() for value in endpoint_text)
            ]
            if dependents:
                findings.append(RelationshipFinding(
                    RelationshipFindingType.INACTIVE_RELATIONSHIP_DEPENDENCY,
                    "PBI_INACTIVE_RELATIONSHIP_DEPENDENCY",
                    f"Inactive relationship {relationship.name} is referenced by: {', '.join(item.name for item in dependents)}.",
                    relationship.object_id,
                ))
    order = {value: index for index, value in enumerate(RelationshipFindingType)}
    return tuple(sorted(findings, key=lambda item: (order[item.finding_type], item.code, item.relationship_id or item.canonical_relationship or "")))


def _confidence_for_classification(
    classification: ImportSupportClassification,
) -> SupportConfidence:
    if classification is ImportSupportClassification.SUPPORTED_EXACT:
        return SupportConfidence.PROVEN
    if classification in {
        ImportSupportClassification.SUPPORTED_WITH_MAPPING,
        ImportSupportClassification.SUPPORTED_WITH_ASSUMPTIONS,
    }:
        return SupportConfidence.CONDITIONAL
    if classification in {
        ImportSupportClassification.TARGET_SPECIFIC,
        ImportSupportClassification.UNSUPPORTED,
    }:
        return SupportConfidence.NOT_APPLICABLE
    return SupportConfidence.UNRESOLVED


def build_object_support_records(
    inventory: PowerBIModelInventory,
    candidates: Sequence[ImportMetricCandidate | Mapping[str, Any]] = (),
    relationship_findings: Sequence[RelationshipFinding | Mapping[str, Any]] = (),
) -> tuple[ObjectSupportRecord, ...]:
    """Classify every inventoried object with deterministic, auditable rules."""

    candidate_by_id: dict[str, ImportMetricCandidate] = {}
    for raw in candidates:
        candidate = (
            raw
            if isinstance(raw, ImportMetricCandidate)
            else ImportMetricCandidate.from_dict(raw)
        )
        candidate_by_id[candidate.source_object_id] = candidate

    findings_by_relationship: dict[str, list[RelationshipFinding]] = {}
    for raw in relationship_findings:
        finding = (
            raw if isinstance(raw, RelationshipFinding) else RelationshipFinding.from_dict(raw)
        )
        if finding.relationship_id:
            findings_by_relationship.setdefault(finding.relationship_id, []).append(finding)

    records: list[ObjectSupportRecord] = []

    def target_specific(
        object_id: str,
        object_kind: str,
        object_name: str,
        location: SourceLocation | None,
        *,
        rule: str,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        records.append(
            ObjectSupportRecord(
                object_id=object_id,
                object_kind=object_kind,
                object_name=object_name,
                source_location=location,
                classification=ImportSupportClassification.TARGET_SPECIFIC,
                confidence=SupportConfidence.NOT_APPLICABLE,
                classifier_rule_id=rule,
                dependencies=dependencies,
            )
        )

    target_specific(
        inventory.model.object_id,
        "MODEL",
        inventory.model.name,
        SourceLocation("model.tmdl", 1, 1),
        rule="POWERBI_MODEL_DISCOVERY_EVIDENCE",
    )
    for table in inventory.tables:
        target_specific(
            table.object_id,
            "TABLE",
            table.name,
            table.source_location,
            rule=(
                "POWERBI_CALCULATED_TABLE_TARGET_SPECIFIC"
                if table.is_calculated
                else "POWERBI_PHYSICAL_TABLE_TARGET_SPECIFIC"
            ),
        )
    for column in inventory.columns:
        target_specific(
            column.object_id,
            "CALCULATED_COLUMN" if column.is_calculated else "COLUMN",
            f"{column.table}[{column.name}]",
            column.source_location,
            rule=(
                "POWERBI_CALCULATED_COLUMN_TARGET_SPECIFIC"
                if column.is_calculated
                else "POWERBI_PHYSICAL_COLUMN_TARGET_SPECIFIC"
            ),
        )
    for measure in inventory.measures:
        candidate = candidate_by_id.get(measure.object_id)
        if candidate is not None:
            classification = candidate.classification
            records.append(
                ObjectSupportRecord(
                    object_id=measure.object_id,
                    object_kind="MEASURE",
                    object_name=f"{measure.table}[{measure.name}]",
                    source_location=measure.source_location,
                    classification=classification,
                    confidence=_confidence_for_classification(classification),
                    classifier_rule_id=f"POWERBI_MEASURE_{classification.value}",
                    dependencies=measure.dependency_object_ids,
                    recognized_pattern=candidate.recognized_pattern,
                    required_mappings=candidate.mapping.required_mappings,
                    assumptions=candidate.assumptions,
                    unsupported_constructs=candidate.unsupported_constructs,
                    diagnostics=candidate.diagnostics,
                )
            )
            continue
        diagnostic_codes = {item.code for item in measure.analysis.diagnostics}
        if measure.analysis.supported:
            classification = ImportSupportClassification.SUPPORTED_WITH_MAPPING
            rule = "POWERBI_DAX_SUPPORTED_MAPPING_PENDING"
        elif diagnostic_codes & _MANUAL_DAX_CODES:
            classification = ImportSupportClassification.MANUAL_REVIEW_REQUIRED
            rule = "POWERBI_DAX_MANUAL_REVIEW_REQUIRED"
        else:
            classification = ImportSupportClassification.UNSUPPORTED
            rule = "POWERBI_DAX_UNSUPPORTED"
        records.append(
            ObjectSupportRecord(
                object_id=measure.object_id,
                object_kind="MEASURE",
                object_name=f"{measure.table}[{measure.name}]",
                source_location=measure.source_location,
                classification=classification,
                confidence=_confidence_for_classification(classification),
                classifier_rule_id=rule,
                dependencies=measure.dependency_object_ids,
                recognized_pattern=measure.analysis.pattern,
                required_mappings=("canonical.metric",) if measure.analysis.supported else (),
                unsupported_constructs=tuple(sorted(diagnostic_codes)),
                diagnostics=measure.analysis.diagnostics,
            )
        )
    for relationship in inventory.relationships:
        findings = findings_by_relationship.get(relationship.object_id, [])
        finding_types = {item.finding_type for item in findings}
        if finding_types & {
            RelationshipFindingType.MANUAL_REVIEW_REQUIRED,
            RelationshipFindingType.INACTIVE_RELATIONSHIP_DEPENDENCY,
            RelationshipFindingType.AMBIGUOUS_FILTER_PATH,
        }:
            classification = ImportSupportClassification.MANUAL_REVIEW_REQUIRED
            rule = "POWERBI_RELATIONSHIP_MANUAL_REVIEW_REQUIRED"
        elif RelationshipFindingType.EXACT_MATCH in finding_types:
            classification = ImportSupportClassification.SUPPORTED_EXACT
            rule = "POWERBI_RELATIONSHIP_EXACT_MATCH"
        else:
            classification = ImportSupportClassification.TARGET_SPECIFIC
            rule = "POWERBI_RELATIONSHIP_TARGET_SPECIFIC"
        records.append(
            ObjectSupportRecord(
                object_id=relationship.object_id,
                object_kind="RELATIONSHIP",
                object_name=relationship.name,
                source_location=relationship.source_location,
                classification=classification,
                confidence=_confidence_for_classification(classification),
                classifier_rule_id=rule,
                dependencies=(
                    f"{relationship.from_table}[{relationship.from_column}]",
                    f"{relationship.to_table}[{relationship.to_column}]",
                ),
                diagnostics=tuple(
                    ImportDiagnostic(item.code, item.message) for item in findings
                ),
            )
        )
    for hierarchy in inventory.hierarchies:
        target_specific(
            hierarchy.object_id,
            "HIERARCHY",
            f"{hierarchy.table}[{hierarchy.name}]",
            hierarchy.source_location,
            rule="POWERBI_HIERARCHY_TARGET_SPECIFIC",
            dependencies=hierarchy.levels,
        )
    for group in inventory.calculation_groups:
        target_specific(
            group.object_id,
            "CALCULATION_GROUP",
            group.table,
            group.source_location,
            rule="POWERBI_CALCULATION_GROUP_TARGET_SPECIFIC",
            dependencies=group.items,
        )
    for role in inventory.roles:
        target_specific(
            role.object_id,
            "ROLE_RLS",
            role.name,
            role.source_location,
            rule="POWERBI_ROLE_RLS_TARGET_SPECIFIC",
            dependencies=tuple(table for table, _ in role.table_permissions),
        )
    for partition in inventory.partitions:
        target_specific(
            partition.object_id,
            "PARTITION",
            f"{partition.table}[{partition.name}]",
            partition.source_location,
            rule="POWERBI_PARTITION_TARGET_SPECIFIC",
        )
    for reference in inventory.report_references:
        target_specific(
            reference.object_id,
            "REPORT_REFERENCE",
            f"{reference.report}:{reference.table}[{reference.object_name}]",
            reference.source_location,
            rule="POWERBI_REPORT_REFERENCE_TARGET_SPECIFIC",
            dependencies=(f"{reference.table}[{reference.object_name}]",),
        )

    result = tuple(
        sorted(records, key=lambda item: (item.object_kind, item.object_name.casefold(), item.object_id))
    )
    object_ids = [item.object_id for item in result]
    if len(object_ids) != len(set(object_ids)):
        raise PowerBIImportError("Every inventoried object must have exactly one support record.")
    return result


__all__ = [
    "CANONICAL_SOURCE", "EXTRACTION_VERSION", "INVENTORY_SCHEMA_VERSION", "MAPPING_SCHEMA_VERSION",
    "DaxAnalysis", "DaxFilter", "ImportDiagnostic", "ImportMetricCandidate", "ImportSupportClassification",
    "MappingDecision", "MappingMethod", "MappingValidationError", "ObjectSupportRecord", "PowerBIColumn", "PowerBIImportError",
    "PowerBIMeasure", "PowerBIModelInventory", "PowerBIModelIdentity", "PowerBIPartition", "PowerBIPathError",
    "PowerBIRegenerationComparison", "PowerBIRelationship", "PowerBITable", "RelationshipFinding",
    "RelationshipFindingType", "SourceLocation", "analyze_dax_measure", "analyze_import_relationships",
    "SupportConfidence", "build_import_metric_candidates", "build_object_support_records", "compare_regenerated_powerbi", "extract_powerbi_inventory",
    "inventory_json_bytes", "load_import_mapping_file", "render_inventory_markdown",
    "resolve_import_mappings", "resolve_powerbi_model_dir", "validate_import_mapping",
]
