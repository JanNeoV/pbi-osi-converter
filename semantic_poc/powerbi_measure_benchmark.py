from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from semantic_poc.agent.powerbi_import import analyze_dax_measure


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "semantic_poc" / "benchmark" / "pbi_trial" / "measure-cases.yml"
DEFAULT_V2_SPEC = (
    REPO_ROOT
    / "semantic_poc"
    / "benchmark"
    / "pbi_trial_v2"
    / "measure-cases.yml"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "semantic_poc" / "output" / "powerbi_benchmarks"
PROTECTED_REPOSITORY_FILES = (
    "models/semantic/triathlon_semantic.yml",
    "semantic/triathlon_metric_contract.yml",
    "semantic_poc/output/dbt_semantics.json",
    "semantic_poc/output/powerbi_semantics.json",
    "semantic_poc/output/proposed_powerbi_patch.json",
    "semantic_poc/output/snowflake_semantic_view.yml",
)
BENCHMARK_ID_PATTERN = re.compile(r"^bench_[0-9a-f]{16}$")
CASE_ID_PATTERNS = {
    1: re.compile(r"^pbim_[0-9]{3}$"),
    2: re.compile(r"^pbiv2_[0-9]{3}$"),
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_PATTERN = re.compile(r"^[^\x00-\x1f]{1,256}$")
MAX_DAX_LENGTH = 16 * 1024
SUPPORTED_REVIEW_CATEGORIES = frozenset(
    {
        "EXACT_CONVERSION",
        "MANUAL_REVIEW_REQUIRED",
        "SNOWFLAKE_SUPPORTED_LOCAL_IR_GAP",
        "SEMANTIC_TRAP",
        "SNOWFLAKE_UNSUPPORTED",
        "RELATIONSHIP_OR_CONTEXT_REVIEW",
    }
)
SUPPORTED_OPERATIONS = frozenset({"PRESERVE", "UPDATE", "ADD"})
SUPPORTED_SEMANTIC_STATES = frozenset({"CORRECT", "INTENTIONAL_DEFECT"})
APPROVED_PARSER_EXPECTATION_MIGRATIONS = frozenset(
    {
        ("pbi_trial_autopilot_measure_conversion", "pbim_004", "SCALED_METRIC"),
        ("pbi_trial_autopilot_measure_conversion", "pbim_005", "SCALED_METRIC"),
        ("pbi_trial_autopilot_measure_conversion", "pbim_006", "SCALED_METRIC"),
        ("pbi_trial_autopilot_measure_conversion", "pbim_010", "AVERAGE"),
        ("pbi_trial_autopilot_measure_conversion", "pbim_020", "SUM_ADDITION"),
        ("pbi_trial_autopilot_measure_conversion", "pbim_021", "METRIC_ADDITION"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_009", "SUM_ADDITION"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_010", "METRIC_ADDITION"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_011", "FILTERED_COUNT"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_012", "FILTERED_COUNT"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_013", "FILTERED_COUNT"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_020", "AVERAGE"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_021", "MIN"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_022", "MAX"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_026", "SCALED_METRIC"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_027", "SCALED_METRIC"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_028", "SCALED_METRIC"),
        ("pbi_trial_measure_conversion_v2", "pbiv2_041", "SCALED_METRIC"),
    }
)


class PowerBIBenchmarkError(ValueError):
    """Raised when a benchmark cannot be created or verified safely."""


def _parser_expectation_matches(
    expected: bool,
    analysis: Any,
    *,
    benchmark_name: str,
    case_id: str,
) -> bool:
    return analysis.supported is expected or (
        expected is False
        and analysis.supported
        and (benchmark_name, case_id, analysis.pattern)
        in APPROVED_PARSER_EXPECTATION_MIGRATIONS
    )


@dataclass(frozen=True)
class MeasureBlock:
    name: str
    description_start: int
    start: int
    end: int
    expression: str


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_id: str
    run_dir: Path
    project_file: Path
    source_definition_sha256: str
    copied_project_sha256: str
    measure_count: int


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _require_within(path: Path, root: Path, *, label: str) -> Path:
    resolved = path.resolve()
    if not _is_within(resolved, root):
        raise PowerBIBenchmarkError(f"{label} must resolve inside {root.resolve()}.")
    return resolved


def _assert_no_symlinks(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise PowerBIBenchmarkError(f"{label} cannot be a symbolic link or junction: {path.name}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise PowerBIBenchmarkError(
                    f"{label} contains a symbolic link or junction: {child.relative_to(path).as_posix()}"
                )


def _tree_hash(paths: Sequence[tuple[str, Path]], *, exclude_local_state: bool) -> str:
    digest = hashlib.sha256()
    records: list[tuple[str, Path]] = []
    for prefix, root in paths:
        if not root.exists():
            raise PowerBIBenchmarkError(f"Required benchmark source is missing: {prefix}")
        _assert_no_symlinks(root, label=prefix)
        if root.is_file():
            records.append((prefix, root))
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if exclude_local_state and (
                any(part.casefold() == ".pbi" for part in relative.parts)
                or path.suffix.casefold() in {".abf", ".pbix"}
            ):
                continue
            records.append((f"{prefix}/{relative.as_posix()}", path))
    for relative, path in sorted(records, key=lambda item: item[0].casefold()):
        name = relative.replace("\\", "/").encode("utf-8")
        content_hash = _sha256_file(path).encode("ascii")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(content_hash)
    return digest.hexdigest()


def _repository_hashes(
    repository_root: Path,
    protected_paths: Sequence[str] = PROTECTED_REPOSITORY_FILES,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in protected_paths:
        path = repository_root / relative
        if not path.is_file():
            raise PowerBIBenchmarkError(f"Protected repository file is missing: {relative}")
        result[relative] = _sha256_file(path)
    return result


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PowerBIBenchmarkError(f"{label} must be a mapping.")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not SAFE_NAME_PATTERN.fullmatch(value):
        raise PowerBIBenchmarkError(f"{label} must be non-empty safe text.")
    return value


def _required_dax_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PowerBIBenchmarkError(f"{label} must be non-empty DAX text.")
    if len(value) > MAX_DAX_LENGTH:
        raise PowerBIBenchmarkError(
            f"{label} exceeds the {MAX_DAX_LENGTH}-character safety limit."
        )
    if any(
        (ord(character) < 32 or 127 <= ord(character) <= 159)
        and character not in {"\t", "\r", "\n"}
        for character in value
    ):
        raise PowerBIBenchmarkError(f"{label} contains an unsafe control character.")
    return value


def _operation_counts(spec: Mapping[str, Any]) -> Counter[str]:
    return Counter(str(item["operation"]) for item in spec["measures"])


def load_measure_spec(path: str | Path = DEFAULT_SPEC) -> dict[str, Any]:
    spec_path = Path(path).resolve()
    loaded = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    spec = dict(_required_mapping(loaded, "Benchmark specification"))
    expected_top = {
        "schema_version",
        "benchmark_name",
        "source_project",
        "expected_measure_count",
        "comparison",
        "review_categories",
        "measures",
    }
    if set(spec) != expected_top:
        raise PowerBIBenchmarkError(
            f"Benchmark specification fields must be exactly: {', '.join(sorted(expected_top))}."
        )
    schema_version = spec["schema_version"]
    if schema_version not in CASE_ID_PATTERNS:
        raise PowerBIBenchmarkError(
            "Benchmark specification schema_version must be 1 or 2."
        )
    _required_text(spec["benchmark_name"], "benchmark_name")
    source = _required_mapping(spec["source_project"], "source_project")
    source_fields = {
        "project_file",
        "semantic_model_dir",
        "report_dir",
        "measure_table",
        "measure_file",
    }
    if set(source) != source_fields:
        raise PowerBIBenchmarkError(
            f"source_project fields must be exactly: {', '.join(sorted(source_fields))}."
        )
    for key in source_fields:
        _required_text(source[key], f"source_project.{key}")
    if Path(str(source["measure_file"])).is_absolute() or ".." in Path(
        str(source["measure_file"])
    ).parts:
        raise PowerBIBenchmarkError("source_project.measure_file must be a safe relative path.")

    categories = spec["review_categories"]
    if (
        not isinstance(categories, list)
        or not categories
        or any(not isinstance(value, str) for value in categories)
        or len(categories) != len(set(categories))
        or not set(categories).issubset(SUPPORTED_REVIEW_CATEGORIES)
    ):
        raise PowerBIBenchmarkError(
            "review_categories must be a unique non-empty subset of the supported categories."
        )
    category_set = set(categories)
    measures = spec["measures"]
    if not isinstance(measures, list):
        raise PowerBIBenchmarkError("measures must be an array.")
    if (
        not isinstance(spec["expected_measure_count"], int)
        or isinstance(spec["expected_measure_count"], bool)
        or spec["expected_measure_count"] <= 0
        or spec["expected_measure_count"] != len(measures)
    ):
        raise PowerBIBenchmarkError("expected_measure_count must equal the measure manifest length.")

    case_ids: set[str] = set()
    names: set[str] = set()
    operation_counts: Counter[str] = Counter()
    allowed_operations = (
        frozenset({"PRESERVE", "ADD"})
        if schema_version == 1
        else frozenset({"UPDATE", "ADD"})
    )
    required_measure_fields = {
        "case_id",
        "name",
        "operation",
        "dax",
        "description",
        "display_folder",
        "format_string",
        "source_unit",
        "target_unit",
        "semantic_status",
        "expected_review_category",
        "expected_local_parser_supported",
        "intentional_defects",
        "correction",
    }
    for index, raw in enumerate(measures):
        item = _required_mapping(raw, f"measures[{index}]")
        if set(item) != required_measure_fields:
            raise PowerBIBenchmarkError(
                f"measures[{index}] fields must be exactly: "
                + ", ".join(sorted(required_measure_fields))
                + "."
            )
        case_id = _required_text(item["case_id"], f"measures[{index}].case_id")
        if not CASE_ID_PATTERNS[schema_version].fullmatch(case_id) or case_id in case_ids:
            raise PowerBIBenchmarkError(f"Invalid or duplicate measure case_id: {case_id}")
        case_ids.add(case_id)
        name = _required_text(item["name"], f"measures[{index}].name")
        identity = name.casefold()
        if identity in names:
            raise PowerBIBenchmarkError(f"Duplicate case-insensitive measure name: {name}")
        names.add(identity)
        operation = item["operation"]
        if operation not in allowed_operations:
            raise PowerBIBenchmarkError(f"Unsupported measure operation: {operation}")
        operation_counts[str(operation)] += 1
        dax = _required_dax_text(item["dax"], f"measures[{index}].dax")
        _required_text(item["description"], f"measures[{index}].description")
        _required_text(item["display_folder"], f"measures[{index}].display_folder")
        _required_text(item["source_unit"], f"measures[{index}].source_unit")
        _required_text(item["target_unit"], f"measures[{index}].target_unit")
        if item["format_string"] is not None:
            _required_text(item["format_string"], f"measures[{index}].format_string")
        if item["semantic_status"] not in SUPPORTED_SEMANTIC_STATES:
            raise PowerBIBenchmarkError(
                f"Unsupported semantic_status: {item['semantic_status']}"
            )
        if item["expected_review_category"] not in category_set:
            raise PowerBIBenchmarkError(
                "expected_review_category must be declared in review_categories: "
                f"{item['expected_review_category']}"
            )
        if not isinstance(item["expected_local_parser_supported"], bool):
            raise PowerBIBenchmarkError("expected_local_parser_supported must be Boolean.")
        analysis = analyze_dax_measure(dax)
        if not _parser_expectation_matches(
            item["expected_local_parser_supported"],
            analysis,
            benchmark_name=str(spec["benchmark_name"]),
            case_id=case_id,
        ):
            raise PowerBIBenchmarkError(
                f"Local-parser expectation drift for {name}: "
                f"expected {item['expected_local_parser_supported']}, got {analysis.supported}."
            )
        defects = item["intentional_defects"]
        if not isinstance(defects, list) or any(not isinstance(value, str) for value in defects):
            raise PowerBIBenchmarkError("intentional_defects must be an array of strings.")
        if bool(defects) != (item["semantic_status"] == "INTENTIONAL_DEFECT"):
            raise PowerBIBenchmarkError(
                f"Semantic defect state does not match intentional_defects for {name}."
            )
        correction = item["correction"]
        if correction is not None and not isinstance(correction, Mapping):
            raise PowerBIBenchmarkError("correction must be a mapping or null.")
        if (
            isinstance(correction, Mapping)
            and correction.get("expected_source_dax") is not None
        ):
            _required_dax_text(
                correction["expected_source_dax"],
                f"measures[{index}].correction.expected_source_dax",
            )
    prefix = "pbim" if schema_version == 1 else "pbiv2"
    expected_case_ids = {
        f"{prefix}_{index:03d}"
        for index in range(1, spec["expected_measure_count"] + 1)
    }
    if case_ids != expected_case_ids:
        raise PowerBIBenchmarkError(
            f"case_id values must cover {prefix}_001 through "
            f"{prefix}_{spec['expected_measure_count']:03d} exactly."
        )
    required_existing_operation = "PRESERVE" if schema_version == 1 else "UPDATE"
    if operation_counts[required_existing_operation] <= 0 or operation_counts["ADD"] <= 0:
        raise PowerBIBenchmarkError(
            f"Schema version {schema_version} requires at least one "
            f"{required_existing_operation} and one ADD operation."
        )
    return spec


def measure_spec_sha256(spec: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(spec))


def _unquote_identifier(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("'") and stripped.endswith("'"):
        return stripped[1:-1].replace("''", "'")
    return stripped


def _parse_measure_blocks(text: str) -> tuple[MeasureBlock, ...]:
    lines = text.splitlines(keepends=True)
    starts = [
        index for index, line in enumerate(lines) if re.match(r"^\tmeasure\s+", line)
    ]
    blocks: list[MeasureBlock] = []
    for start in starts:
        description_start = start
        while (
            description_start > 0
            and re.match(r"^\t///(?:\s|$)", lines[description_start - 1])
        ):
            description_start -= 1
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if re.match(r"^\t(?:measure|column|partition|hierarchy)\s+", lines[index]):
                end = index
                if lines[index].startswith("\tmeasure "):
                    while end > start + 1 and re.match(
                        r"^\t///(?:\s|$)", lines[end - 1]
                    ):
                        end -= 1
                break
        declaration = lines[start].strip()[len("measure ") :]
        if "=" not in declaration:
            raise PowerBIBenchmarkError("Every benchmark measure declaration must contain '='.")
        raw_name, initial = declaration.split("=", 1)
        name = _unquote_identifier(raw_name)
        expression_lines: list[str] = []
        if initial.strip():
            expression_lines.append(initial.strip())
        for line in lines[start + 1 : end]:
            if re.match(
                r"^\t\t(?:formatString|displayFolder|lineageTag|annotation)\b", line
            ):
                break
            stripped = line.strip()
            if stripped:
                expression_lines.append(stripped)
        expression = "\n".join(expression_lines).strip()
        blocks.append(
            MeasureBlock(
                name=name,
                description_start=description_start,
                start=start,
                end=end,
                expression=expression,
            )
        )
    identities = [block.name.casefold() for block in blocks]
    if len(identities) != len(set(identities)):
        raise PowerBIBenchmarkError("TMDL measure names must be case-insensitively unique.")
    return tuple(blocks)


def _normalized_dax(expression: str) -> str:
    normalized = analyze_dax_measure(expression).normalized_expression
    return normalized or _fallback_normalized_dax(expression)


def _fallback_normalized_dax(expression: str) -> str:
    """Normalize unsupported DAX without losing token or string boundaries."""

    tokens: list[str] = []
    index = 0
    length = len(expression)
    while index < length:
        character = expression[index]
        if character.isspace():
            index += 1
            continue
        if expression.startswith("//", index):
            newline = expression.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if expression.startswith("/*", index):
            end = expression.find("*/", index + 2)
            if end < 0:
                tokens.append(expression[index:].casefold())
                break
            index = end + 2
            continue
        if character in {'"', "'"}:
            quote = character
            end = index + 1
            while end < length:
                if expression[end] == quote:
                    if end + 1 < length and expression[end + 1] == quote:
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            tokens.append(expression[index:end])
            index = end
            continue
        if character == "[":
            end = expression.find("]", index + 1)
            if end < 0:
                tokens.append(expression[index:].casefold())
                break
            tokens.append(expression[index : end + 1].casefold())
            index = end + 1
            continue
        if character.isalnum() or character in {"_", "$"}:
            end = index + 1
            while end < length and (
                expression[end].isalnum() or expression[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(expression[index:end].casefold())
            index = end
            continue
        operator = next(
            (
                candidate
                for candidate in ("&&", "||", "<=", ">=", "<>", "==", "=>")
                if expression.startswith(candidate, index)
            ),
            None,
        )
        if operator is not None:
            tokens.append(operator)
            index += len(operator)
            continue
        tokens.append(character.casefold())
        index += 1
    return "\x1f".join(tokens)


def _escape_tmdl_name(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_measure_expression(name: str, dax: str) -> list[str]:
    normalized = dax.replace("\r\n", "\n").replace("\r", "\n").strip()
    dax_lines = normalized.split("\n")
    declaration = f"\tmeasure {_escape_tmdl_name(name)} ="
    if len(dax_lines) == 1:
        return [f"{declaration} {dax_lines[0]}"]
    return [declaration, *(f"\t\t\t{line.rstrip()}" for line in dax_lines)]


def _render_measure_header(item: Mapping[str, Any]) -> list[str]:
    description = str(item["description"]).replace("\r", " ").replace("\n", " ")
    return [
        f"\t/// {description}",
        *_render_measure_expression(str(item["name"]), str(item["dax"])),
    ]


def _metadata_entries(
    lines: Sequence[str],
    block: MeasureBlock,
) -> list[tuple[str, list[str]]]:
    metadata_start: int | None = None
    for index in range(block.start + 1, block.end):
        if re.match(r"^\t\t[A-Za-z][A-Za-z0-9_]*\b", lines[index]):
            metadata_start = index
            break
    if metadata_start is None:
        return []

    entries: list[tuple[str, list[str]]] = []
    current_key: str | None = None
    current_lines: list[str] = []
    for line in lines[metadata_start : block.end]:
        match = re.match(r"^\t\t([A-Za-z][A-Za-z0-9_]*)\b", line)
        if match is not None:
            if current_key is not None:
                entries.append((current_key, current_lines))
            current_key = match.group(1)
            current_lines = [line]
        elif current_key is not None:
            current_lines.append(line)
    if current_key is not None:
        entries.append((current_key, current_lines))
    return entries


def _render_updated_measure(
    lines: Sequence[str],
    block: MeasureBlock,
    item: Mapping[str, Any],
    *,
    newline: str,
) -> list[str]:
    entries = _metadata_entries(lines, block)
    lineage_entries = [entry for entry in entries if entry[0].casefold() == "lineagetag"]
    if len(lineage_entries) != 1:
        raise PowerBIBenchmarkError(
            f"Updated source measure {block.name} must have exactly one lineageTag."
        )
    preserved_entries = [
        entry_lines
        for key, entry_lines in entries
        if key.casefold()
        not in {"formatstring", "formatstringdefinition", "displayfolder"}
    ]
    rendered = [f"{line}{newline}" for line in _render_measure_header(item)]
    if item["format_string"] is not None:
        rendered.append(f"\t\tformatString: {item['format_string']}{newline}")
    rendered.append(f"\t\tdisplayFolder: {item['display_folder']}{newline}")
    for entry_lines in preserved_entries:
        rendered.extend(entry_lines)
    if not rendered[-1].endswith(newline):
        rendered[-1] += newline
    if rendered[-1].strip():
        rendered.append(newline)
    return rendered


def _render_new_measure(
    benchmark_name: str,
    item: Mapping[str, Any],
    *,
    newline: str,
) -> str:
    name = str(item["name"])
    lineage = uuid.uuid5(uuid.NAMESPACE_URL, f"{benchmark_name}/measure/{name}")
    lines = _render_measure_header(item)
    if item["format_string"] is not None:
        lines.append(f"\t\tformatString: {item['format_string']}")
    lines.extend(
        [
            f"\t\tdisplayFolder: {item['display_folder']}",
            f"\t\tlineageTag: {lineage}",
            "",
        ]
    )
    return newline.join(lines) + newline


def render_measures_tmdl(source_text: str, spec: Mapping[str, Any]) -> str:
    newline = "\r\n"
    normalized_source = source_text.replace("\r\n", "\n").replace("\r", "\n")
    text = normalized_source.replace("\n", newline)
    lines = text.splitlines(keepends=True)
    blocks = {block.name.casefold(): block for block in _parse_measure_blocks(text)}
    preserve = [item for item in spec["measures"] if item["operation"] == "PRESERVE"]
    update = [item for item in spec["measures"] if item["operation"] == "UPDATE"]
    add = [item for item in spec["measures"] if item["operation"] == "ADD"]
    expected_existing = {
        str(item["name"]).casefold() for item in [*preserve, *update]
    }
    if set(blocks) != expected_existing:
        missing = sorted(
            expected_existing - set(blocks)
        )
        unexpected = sorted(set(blocks) - expected_existing)
        raise PowerBIBenchmarkError(
            f"Source measure set drifted; missing={missing}, unexpected={unexpected}."
        )
    for item in preserve:
        block = blocks[str(item["name"]).casefold()]
        if _normalized_dax(block.expression) != _normalized_dax(str(item["dax"])):
            raise PowerBIBenchmarkError(f"Preserved source DAX drifted for {item['name']}.")

    for item in update:
        correction = item.get("correction")
        expected_source_dax = (
            correction.get("expected_source_dax")
            if isinstance(correction, Mapping)
            else None
        )
        if expected_source_dax is None:
            continue
        block = blocks[str(item["name"]).casefold()]
        if _normalized_dax(block.expression) != _normalized_dax(
            str(expected_source_dax)
        ):
            raise PowerBIBenchmarkError(
                f"Updated source DAX drifted for {item['name']}."
            )

    # Replace from bottom to top so source indexes remain stable. UPDATE changes
    # benchmark-owned metadata while retaining lineage and unknown annotations.
    for item in sorted(
        update,
        key=lambda value: blocks[str(value["name"]).casefold()].start,
        reverse=True,
    ):
        block = blocks[str(item["name"]).casefold()]
        lines[block.description_start : block.end] = _render_updated_measure(
            lines,
            block,
            item,
            newline=newline,
        )

    text = "".join(lines)
    lines = text.splitlines(keepends=True)
    blocks = {block.name.casefold(): block for block in _parse_measure_blocks(text)}

    # Insert folders from bottom to top so earlier source indexes remain stable.
    for item in sorted(
        preserve,
        key=lambda value: blocks[str(value["name"]).casefold()].start,
        reverse=True,
    ):
        block = blocks[str(item["name"]).casefold()]
        existing = next(
            (
                line
                for line in lines[block.start : block.end]
                if re.match(r"^\t\tdisplayFolder\s*:", line)
            ),
            None,
        )
        desired = f"\t\tdisplayFolder: {item['display_folder']}{newline}"
        if existing is not None:
            if existing != desired:
                raise PowerBIBenchmarkError(
                    f"Existing display folder conflicts with the benchmark for {item['name']}."
                )
            continue
        insertion = next(
            (
                index
                for index in range(block.start + 1, block.end)
                if re.match(r"^\t\tlineageTag\s*:", lines[index])
            ),
            block.start + 1,
        )
        lines.insert(insertion, desired)

    text = "".join(lines)
    column_match = re.search(r"(?m)^\tcolumn\s+DUMMY\b", text)
    if column_match is None:
        raise PowerBIBenchmarkError("The MEASURES_ table must retain its DUMMY column.")
    rendered = "".join(
        _render_new_measure(str(spec["benchmark_name"]), item, newline=newline)
        for item in add
    )
    text = text[: column_match.start()] + rendered + text[column_match.start() :]
    return text


def _parse_table_identifier(declaration: str) -> str:
    value = declaration.strip()
    if value.startswith("'"):
        index = 1
        while index < len(value):
            if value[index] == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                return value[1:index].replace("''", "'")
            index += 1
        raise PowerBIBenchmarkError(f"Unterminated TMDL identifier: {declaration!r}")
    return value.split()[0]


def _table_schema(definition_dir: Path) -> tuple[dict[str, set[str]], dict[str, MeasureBlock]]:
    table_dir = definition_dir / "tables"
    if not table_dir.is_dir():
        raise PowerBIBenchmarkError("Copied semantic model is missing definition/tables.")
    tables: dict[str, set[str]] = {}
    measures: dict[str, MeasureBlock] = {}
    for path in sorted(table_dir.glob("*.tmdl"), key=lambda item: item.name.casefold()):
        text = path.read_text(encoding="utf-8-sig")
        table_match = re.search(r"(?m)^table\s+(.+)$", text)
        if table_match is None:
            raise PowerBIBenchmarkError(f"TMDL file has no table declaration: {path.name}")
        table_name = _parse_table_identifier(table_match.group(1))
        identity = table_name.casefold()
        if identity in tables:
            raise PowerBIBenchmarkError(f"Duplicate table identity: {table_name}")
        columns: set[str] = set()
        for match in re.finditer(r"(?m)^\tcolumn\s+(.+)$", text):
            name = _parse_table_identifier(match.group(1))
            if name.casefold() in columns:
                raise PowerBIBenchmarkError(
                    f"Duplicate column identity in {table_name}: {name}"
                )
            columns.add(name.casefold())
        tables[identity] = columns
        for block in _parse_measure_blocks(text):
            if block.name.casefold() in measures:
                raise PowerBIBenchmarkError(f"Duplicate model measure identity: {block.name}")
            measures[block.name.casefold()] = block
    return tables, measures


def _validate_references(
    tables: Mapping[str, set[str]],
    measures: Mapping[str, MeasureBlock],
) -> None:
    table_expression_functions = {
        "all",
        "allselected",
        "calculatetable",
        "distinct",
        "filter",
        "selectcolumns",
        "summarize",
        "topn",
        "values",
    }
    direct_table_functions = (
        "COUNTROWS",
        "SUMX",
        "AVERAGEX",
        "MEDIANX",
        "PERCENTILEX.INC",
        "STDEVX.P",
        "RANKX",
        "FILTER",
        "VALUES",
        "ALL",
        "ALLSELECTED",
        "REMOVEFILTERS",
        "DISTINCT",
        "TREATAS",
    )
    direct_table_pattern = (
        r"\b("
        + "|".join(re.escape(value) for value in direct_table_functions)
        + r")\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)"
    )
    dependency_graph: dict[str, set[str]] = {}
    for identity, block in measures.items():
        expression = block.expression
        variables = {
            name.casefold()
            for name in re.findall(
                r"\bVAR\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
                expression,
                flags=re.IGNORECASE,
            )
        }
        for table, column in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]", expression
        ):
            table_identity = table.casefold()
            if table_identity not in tables and table_identity not in variables:
                raise PowerBIBenchmarkError(
                    f"{block.name} references missing table {table}."
                )
            if (
                table_identity in tables
                and column.casefold() not in tables[table_identity]
            ):
                raise PowerBIBenchmarkError(
                    f"{block.name} references missing column {table}[{column}]."
                )
        for function, table in re.findall(
            direct_table_pattern,
            expression,
            flags=re.IGNORECASE,
        ):
            table_identity = table.casefold()
            if (
                table_identity not in tables
                and table_identity not in variables
                and table_identity not in table_expression_functions
            ):
                raise PowerBIBenchmarkError(
                    f"{block.name} {function} references missing table {table}."
                )
        for table in re.findall(
            r"\bTOPN\s*\(\s*[^,\r\n]+,\s*([A-Za-z_][A-Za-z0-9_]*)",
            expression,
            flags=re.IGNORECASE,
        ):
            table_identity = table.casefold()
            if (
                table_identity not in tables
                and table_identity not in variables
                and table_identity not in table_expression_functions
            ):
                raise PowerBIBenchmarkError(
                    f"{block.name} TOPN references missing table {table}."
                )
        bracket_references = {
            name.casefold()
            for name in re.findall(r"(?<![A-Za-z0-9_])\[([^\]]+)\]", expression)
        }
        virtual_columns = {
            name.replace('""', '"').casefold()
            for name in re.findall(r'"((?:""|[^"])*)"\s*,', expression)
        }
        dependencies = {
            name for name in bracket_references if name in measures
        }
        missing = sorted(
            name
            for name in bracket_references
            if name not in measures and name not in virtual_columns
        )
        if missing:
            raise PowerBIBenchmarkError(
                f"{block.name} references missing measure(s): {', '.join(missing)}."
            )
        dependency_graph[identity] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise PowerBIBenchmarkError(f"Measure dependency cycle detected at {measures[node].name}.")
        if node in visited:
            return
        visiting.add(node)
        for dependency in dependency_graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for measure in sorted(measures):
        visit(measure)


def validate_benchmark_copy(
    project_dir: Path,
    spec: Mapping[str, Any],
) -> tuple[int, dict[str, bool]]:
    source = spec["source_project"]
    semantic_model = project_dir / str(source["semantic_model_dir"])
    definition = semantic_model / "definition"
    tables, measures = _table_schema(definition)
    expected_names = {str(item["name"]).casefold(): item for item in spec["measures"]}
    if set(measures) != set(expected_names):
        missing = sorted(set(expected_names) - set(measures))
        unexpected = sorted(set(measures) - set(expected_names))
        raise PowerBIBenchmarkError(
            f"Copied measure inventory mismatch; missing={missing}, unexpected={unexpected}."
        )
    local_support: dict[str, bool] = {}
    for identity, block in measures.items():
        item = expected_names[identity]
        if _normalized_dax(block.expression) != _normalized_dax(str(item["dax"])):
            raise PowerBIBenchmarkError(f"Copied DAX mismatch for {item['name']}.")
        analysis = analyze_dax_measure(block.expression)
        supported = analysis.supported
        if not _parser_expectation_matches(
            item["expected_local_parser_supported"],
            analysis,
            benchmark_name=str(spec["benchmark_name"]),
            case_id=str(item["case_id"]),
        ):
            raise PowerBIBenchmarkError(
                f"Copied local-parser classification drifted for {item['name']}."
            )
        local_support[str(item["case_id"])] = supported
    _validate_references(tables, measures)
    local_state = [
        path
        for path in project_dir.rglob("*")
        if path.name.casefold() == ".pbi" or path.suffix.casefold() in {".abf", ".pbix"}
    ]
    if local_state:
        raise PowerBIBenchmarkError("Copied project contains forbidden Power BI local state.")
    return len(measures), local_support


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name.casefold() == ".pbi"
        or Path(name).suffix.casefold() in {".abf", ".pbix"}
    }


def _copy_project(source_root: Path, project_dir: Path, spec: Mapping[str, Any]) -> None:
    source = spec["source_project"]
    project_file = source_root / str(source["project_file"])
    semantic_model = source_root / str(source["semantic_model_dir"])
    report = source_root / str(source["report_dir"])
    if not project_file.is_file() or not semantic_model.is_dir() or not report.is_dir():
        raise PowerBIBenchmarkError(
            "Source project must contain the configured PBIP, SemanticModel, and Report paths."
        )
    for label, path in (
        ("Power BI project file", project_file),
        ("Power BI semantic model", semantic_model),
        ("Power BI report", report),
    ):
        _assert_no_symlinks(path, label=label)
    project_dir.mkdir(parents=True)
    shutil.copy2(project_file, project_dir / project_file.name)
    shutil.copytree(
        semantic_model,
        project_dir / semantic_model.name,
        ignore=_copy_ignore,
    )
    shutil.copytree(report, project_dir / report.name, ignore=_copy_ignore)


def _project_file_hashes(
    project_root: Path,
    spec: Mapping[str, Any],
) -> dict[str, str]:
    source = spec["source_project"]
    result: dict[str, str] = {}
    configured = (
        Path(str(source["project_file"])),
        Path(str(source["semantic_model_dir"])),
        Path(str(source["report_dir"])),
    )
    for configured_path in configured:
        path = project_root / configured_path
        if path.is_file():
            result[configured_path.as_posix()] = _sha256_file(path)
            continue
        if not path.is_dir():
            raise PowerBIBenchmarkError(
                f"Configured project path is missing: {configured_path.as_posix()}"
            )
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            relative = child.relative_to(project_root)
            if (
                any(part.casefold() == ".pbi" for part in relative.parts)
                or child.suffix.casefold() in {".abf", ".pbix"}
            ):
                continue
            result[relative.as_posix()] = _sha256_file(child)
    return result


def _assert_only_measure_file_changed(
    source_root: Path,
    copied_project_root: Path,
    spec: Mapping[str, Any],
) -> None:
    source_hashes = _project_file_hashes(source_root, spec)
    copied_hashes = _project_file_hashes(copied_project_root, spec)
    missing = sorted(set(source_hashes) - set(copied_hashes))
    unexpected = sorted(set(copied_hashes) - set(source_hashes))
    if missing or unexpected:
        raise PowerBIBenchmarkError(
            "Copied project inventory drifted; "
            f"missing={missing}, unexpected={unexpected}."
        )
    changed = {
        relative
        for relative in source_hashes
        if source_hashes[relative] != copied_hashes[relative]
    }
    source = spec["source_project"]
    expected_change = (
        Path(str(source["semantic_model_dir"])) / str(source["measure_file"])
    ).as_posix()
    if changed != {expected_change}:
        raise PowerBIBenchmarkError(
            "Copied project content drifted outside the configured measure file; "
            f"changed={sorted(changed)}, expected={[expected_change]}."
        )


def _manual_evidence_readme(
    benchmark_id: str,
    spec: Mapping[str, Any],
) -> str:
    slices = [
        str(value).casefold().replace("_", "-")
        for value in spec["comparison"]["required_slices"]
    ]
    lines = [
        f"# Manual evidence for `{benchmark_id}`",
        "",
        "This directory is intentionally ignored. After validating the copied PBIP, place the manual artifacts here:",
        "",
        "- `pbi-trial-benchmark.pbit`",
        *(f"- `powerbi-{value}.csv`" for value in slices),
        "- `autopilot-semantic-view.yml`",
        "- `autopilot-warnings.md`",
        *(f"- `snowflake-{value}.csv`" for value in slices),
        "",
        "Missing numerical files are recorded as `NOT_AVAILABLE`; they are never treated as passing evidence.",
        "",
    ]
    return "\n".join(lines)


def _run_readme(
    benchmark_id: str,
    project_file: str,
    *,
    measure_count: int,
    baseline_reference: str,
) -> str:
    return f"""# Power BI measure benchmark `{benchmark_id}`

- Open `project/{project_file}` in Power BI Desktop.
- Confirm that the `MEASURES_` table contains {measure_count} measures.
- Run `{baseline_reference}` in DAX Query View.
- Follow `docs/POWERBI_AUTOPILOT_HANDOFF.md`.
- Store returned local evidence under `manual-evidence/`.

This benchmark is inspection evidence. It does not modify the canonical semantic contract and performs no deployment.
"""


def _baseline_reference(spec_path: Path, repository_root: Path) -> str:
    baseline = spec_path.resolve().parent / "powerbi-baseline.dax"
    for root in (REPO_ROOT, repository_root.resolve()):
        try:
            return baseline.relative_to(root).as_posix()
        except ValueError:
            continue
    return baseline.as_posix()


def _source_paths(source_root: Path, spec: Mapping[str, Any]) -> tuple[tuple[str, Path], ...]:
    source = spec["source_project"]
    return (
        (str(source["project_file"]), source_root / str(source["project_file"])),
        (
            str(source["semantic_model_dir"]),
            source_root / str(source["semantic_model_dir"]),
        ),
        (str(source["report_dir"]), source_root / str(source["report_dir"])),
    )


def _definition_hash(source_root: Path, spec: Mapping[str, Any]) -> str:
    source = spec["source_project"]
    definition = source_root / str(source["semantic_model_dir"]) / "definition"
    return _tree_hash((("definition", definition),), exclude_local_state=True)


def _benchmark_id(source_definition_sha256: str, spec_sha256: str) -> str:
    digest = _sha256_bytes(
        _canonical_json_bytes(
            {
                "source_definition_sha256": source_definition_sha256,
                "measure_spec_sha256": spec_sha256,
            }
        )
    )
    return f"bench_{digest[:16]}"


def _build_benchmark(
    source_root: Path,
    *,
    spec_path: Path,
    output_root: Path,
    repository_root: Path,
    protected_paths: Sequence[str],
) -> BenchmarkResult:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise PowerBIBenchmarkError("Source Power BI project directory does not exist.")
    output_root = _require_within(output_root, repository_root, label="Benchmark output root")
    output_root.mkdir(parents=True, exist_ok=True)
    spec = load_measure_spec(spec_path)
    source_definition_before = _definition_hash(source_root, spec)
    source_project_before = _tree_hash(
        _source_paths(source_root, spec), exclude_local_state=True
    )
    spec_hash = measure_spec_sha256(spec)
    benchmark_id = _benchmark_id(source_definition_before, spec_hash)
    if not BENCHMARK_ID_PATTERN.fullmatch(benchmark_id):
        raise AssertionError("Internal benchmark ID generation failed.")
    destination = output_root / benchmark_id
    if destination.exists():
        raise PowerBIBenchmarkError(
            f"Benchmark run already exists and will not be overwritten: {benchmark_id}"
        )
    repository_before = _repository_hashes(repository_root, protected_paths)
    # tempfile.mkdtemp applies a private ACL on Windows. That ACL survives an
    # atomic directory rename and can prevent Power BI Desktop or Git processes
    # running under the interactive user from traversing the generated copy.
    # A unique ordinary child directory inherits the reviewed output root ACL.
    stage = output_root / f".{benchmark_id}.{uuid.uuid4().hex}.staging"
    stage.mkdir(exist_ok=False)
    _require_within(stage, output_root, label="Benchmark staging directory")
    try:
        project_dir = stage / "project"
        _copy_project(source_root, project_dir, spec)
        source = spec["source_project"]
        measures_file = (
            project_dir
            / str(source["semantic_model_dir"])
            / str(source["measure_file"])
        )
        source_text = measures_file.read_text(encoding="utf-8-sig")
        rendered = render_measures_tmdl(source_text, spec)
        measures_file.write_bytes(rendered.encode("utf-8"))
        measure_count, local_support = validate_benchmark_copy(project_dir, spec)
        _assert_only_measure_file_changed(source_root, project_dir, spec)
        copied_project_hash = _tree_hash(
            (("project", project_dir),), exclude_local_state=True
        )

        source_definition_after = _definition_hash(source_root, spec)
        source_project_after = _tree_hash(
            _source_paths(source_root, spec), exclude_local_state=True
        )
        repository_after = _repository_hashes(repository_root, protected_paths)
        if source_definition_before != source_definition_after:
            raise PowerBIBenchmarkError("Source Power BI definition changed during benchmark creation.")
        if source_project_before != source_project_after:
            raise PowerBIBenchmarkError("Source Power BI project changed during benchmark creation.")
        if repository_before != repository_after:
            raise PowerBIBenchmarkError(
                "A protected canonical, legacy, or generated repository file changed."
            )

        evidence_dir = stage / "evidence"
        evidence_dir.mkdir()
        counts = _operation_counts(spec)
        run: dict[str, Any] = {
            "schema_version": 1,
            "benchmark_id": benchmark_id,
            "benchmark_name": spec["benchmark_name"],
            "authority_state": "EXPERIMENTAL_BENCHMARK_ONLY",
            "canonical_source": "models/semantic/triathlon_semantic.yml",
            "canonical_change_requested": False,
            "source_label": "external:pbi_trial",
            "source_definition_sha256": source_definition_before,
            "source_project_sha256": source_project_before,
            "measure_spec_sha256": spec_hash,
            "copied_project_sha256": copied_project_hash,
            "source_modified": False,
            "deployment_performed": False,
            "measure_count": measure_count,
            "integer_tolerance": "EXACT",
            "decimal_tolerance": "DECIMAL_1E-9",
            "local_parser_support": local_support,
            "protected_repository_hashes": repository_after,
        }
        if counts["PRESERVE"]:
            run["preserved_measure_count"] = counts["PRESERVE"]
        if counts["UPDATE"]:
            run["updated_measure_count"] = counts["UPDATE"]
        if counts["ADD"]:
            run["added_measure_count"] = counts["ADD"]
        (evidence_dir / "benchmark-run.json").write_bytes(_canonical_json_bytes(run))
        source_copy = {
            "schema_version": 1,
            "benchmark_id": benchmark_id,
            "source_definition_before_sha256": source_definition_before,
            "source_definition_after_sha256": source_definition_after,
            "source_project_before_sha256": source_project_before,
            "source_project_after_sha256": source_project_after,
            "copied_project_sha256": copied_project_hash,
            "source_modified": False,
            "protected_repository_hashes_before": repository_before,
            "protected_repository_hashes_after": repository_after,
        }
        (evidence_dir / "source-copy-hashes.json").write_bytes(
            _canonical_json_bytes(source_copy)
        )
        manual = stage / "manual-evidence"
        manual.mkdir()
        (manual / "README.md").write_text(
            _manual_evidence_readme(benchmark_id, spec),
            encoding="utf-8",
            newline="\n",
        )
        project_file = str(source["project_file"])
        (stage / "README.md").write_text(
            _run_readme(
                benchmark_id,
                project_file,
                measure_count=measure_count,
                baseline_reference=_baseline_reference(spec_path, repository_root),
            ),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(stage, destination)
    except Exception:
        if stage.exists():
            _require_within(stage, output_root, label="Failed benchmark staging directory")
            shutil.rmtree(stage)
        raise
    return BenchmarkResult(
        benchmark_id=benchmark_id,
        run_dir=destination,
        project_file=destination / "project" / str(spec["source_project"]["project_file"]),
        source_definition_sha256=source_definition_before,
        copied_project_sha256=copied_project_hash,
        measure_count=measure_count,
    )


def build_benchmark(
    source_project: str | Path,
    *,
    spec_path: str | Path = DEFAULT_SPEC,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> BenchmarkResult:
    return _build_benchmark(
        Path(source_project),
        spec_path=Path(spec_path),
        output_root=Path(output_root),
        repository_root=REPO_ROOT,
        protected_paths=PROTECTED_REPOSITORY_FILES,
    )


def check_benchmark(
    source_project: str | Path,
    *,
    spec_path: str | Path = DEFAULT_SPEC,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    repository_root: Path = REPO_ROOT,
    protected_paths: Sequence[str] = PROTECTED_REPOSITORY_FILES,
) -> BenchmarkResult:
    source_root = Path(source_project).resolve()
    spec = load_measure_spec(spec_path)
    source_definition = _definition_hash(source_root, spec)
    source_project_hash = _tree_hash(
        _source_paths(source_root, spec), exclude_local_state=True
    )
    spec_hash = measure_spec_sha256(spec)
    benchmark_id = _benchmark_id(source_definition, spec_hash)
    run_dir = _require_within(
        Path(output_root) / benchmark_id,
        repository_root,
        label="Benchmark run directory",
    )
    run_path = run_dir / "evidence" / "benchmark-run.json"
    if not run_path.is_file():
        raise PowerBIBenchmarkError(f"Benchmark run does not exist: {benchmark_id}")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if run.get("benchmark_id") != benchmark_id:
        raise PowerBIBenchmarkError("Benchmark evidence ID does not match the source and manifest.")
    if run.get("source_definition_sha256") != source_definition:
        raise PowerBIBenchmarkError("Source definition hash no longer matches benchmark evidence.")
    if run.get("source_project_sha256") != source_project_hash:
        raise PowerBIBenchmarkError("Source project hash no longer matches benchmark evidence.")
    if run.get("measure_spec_sha256") != spec_hash:
        raise PowerBIBenchmarkError("Measure manifest hash no longer matches benchmark evidence.")
    project_dir = run_dir / "project"
    measure_count, _ = validate_benchmark_copy(project_dir, spec)
    _assert_only_measure_file_changed(source_root, project_dir, spec)
    copied_hash = _tree_hash((("project", project_dir),), exclude_local_state=True)
    if run.get("copied_project_sha256") != copied_hash:
        raise PowerBIBenchmarkError("Copied project hash no longer matches benchmark evidence.")
    current_protected = _repository_hashes(repository_root, protected_paths)
    if run.get("protected_repository_hashes") != current_protected:
        raise PowerBIBenchmarkError("Protected repository hashes no longer match benchmark evidence.")
    return BenchmarkResult(
        benchmark_id=benchmark_id,
        run_dir=run_dir,
        project_file=run_dir
        / "project"
        / str(spec["source_project"]["project_file"]),
        source_definition_sha256=source_definition,
        copied_project_sha256=copied_hash,
        measure_count=measure_count,
    )


def render_measure_catalogue(spec: Mapping[str, Any]) -> str:
    lines = [
        "# Power BI Autopilot Measure Catalogue",
        "",
        "This catalogue is generated from `measure-cases.yml`. Expected categories are test hypotheses; only returned Autopilot YAML and result evidence establish observed outcomes.",
        "",
        "| Case | Measure | Action | Expected review | Local parser | Semantic state |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in spec["measures"]:
        local = "SUPPORTED" if item["expected_local_parser_supported"] else "IR GAP"
        lines.append(
            f"| `{item['case_id']}` | `{item['name']}` | `{item['operation']}` | "
            f"`{item['expected_review_category']}` | `{local}` | `{item['semantic_status']}` |"
        )
    lines.extend(
        [
            "",
            "## Review rules",
            "",
            "- Generated syntax is not evidence of correct business meaning.",
            "- A seconds-to-hours metric requires divisor `3600`; `/60` remains a semantic failure even when targets agree numerically.",
            "- Identifier sums, non-additive ratios, formats, descriptions, and relationship context are reviewed independently.",
            "- `MANUAL_REVIEW_REQUIRED` is mandatory whenever equivalence is ambiguous.",
            "- Integer comparisons are exact; decimal comparisons use absolute tolerance `1e-9`.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify the isolated pbi_trial Power BI measure benchmark."
    )
    parser.add_argument(
        "--source-project",
        required=True,
        help="Directory containing the source pbi_trial PBIP project.",
    )
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.check:
            result = check_benchmark(
                args.source_project,
                spec_path=args.spec,
                output_root=args.output_root,
            )
            status = "POWERBI_MEASURE_BENCHMARK_VERIFIED"
        else:
            result = build_benchmark(
                args.source_project,
                spec_path=args.spec,
                output_root=args.output_root,
            )
            status = "POWERBI_MEASURE_BENCHMARK_CREATED"
    except (OSError, PowerBIBenchmarkError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"POWERBI_MEASURE_BENCHMARK_NOT_ACCEPTED: {exc}")
        return 1
    print(status)
    print(f"BENCHMARK_ID={result.benchmark_id}")
    print(f"MEASURE_COUNT={result.measure_count}")
    print(f"PROJECT_FILE={result.project_file}")
    print("SOURCE_MODIFIED=NO")
    print("CANONICAL_MODIFIED=NO")
    print("DEPLOYMENT_PERFORMED=NO")
    return 0


__all__ = [
    "BenchmarkResult",
    "DEFAULT_SPEC",
    "DEFAULT_V2_SPEC",
    "PowerBIBenchmarkError",
    "build_benchmark",
    "check_benchmark",
    "load_measure_spec",
    "main",
    "measure_spec_sha256",
    "render_measure_catalogue",
    "render_measures_tmdl",
    "validate_benchmark_copy",
]
