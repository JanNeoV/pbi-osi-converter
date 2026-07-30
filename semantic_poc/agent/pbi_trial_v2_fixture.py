from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable

from .powerbi_import import PowerBIModelInventory, extract_powerbi_inventory
from .powerbi_snowflake_audit import powerbi_structure_sha256


FIXTURE_SCHEMA_VERSION = 1
CONNECTION_NORMALIZER_VERSION = "snowflake-location-v1"
REVIEWED_SOURCE_TREE_SHA256 = (
    "5208d521062039a2cbb3d7ab857618b179a5ee781f4fc9d489807045a89bc804"
)
REVIEWED_RAW_STRUCTURE_SHA256 = (
    "7b8c842c08dc88b0a4b11efaef22afe3c0da22304ef908101046f7232209baf4"
)
EXPECTED_COUNTS = {
    "columns": 61,
    "measures": 46,
    "relationships": 7,
    "tables": 9,
}
EXCLUDED_CONTENT_CLASSES = [
    "CREDENTIALS_AND_PROFILES",
    "DATA_EXTRACTS_AND_CACHES",
    "DIAGRAM_LAYOUT",
    "LOCAL_POWER_BI_STATE",
    "REPORT_FILES",
]
_STATIC_INCLUDED = (
    "definition.pbism",
    "definition/database.tmdl",
    "definition/model.tmdl",
    "definition/relationships.tmdl",
)
_SNOWFLAKE_SOURCE = re.compile(
    r'Snowflake\.Databases\(\s*"(?P<account>(?:[^"]|"")*)"\s*,\s*'
    r'"(?P<warehouse>(?:[^"]|"")*)"\s*,',
    re.IGNORECASE,
)
_NAVIGATION = re.compile(
    r'Name="(?P<name>(?:[^"]|"")*)"\s*,\s*Kind="(?P<kind>Database|Schema)"',
    re.IGNORECASE,
)


class FixtureError(RuntimeError):
    """The portable fixture cannot be proven safe and equivalent."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_tree_sha256(path: Path) -> str:
    records: list[dict[str, str]] = []
    for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if item.is_symlink():
            raise FixtureError(f"Fixture trees cannot contain symlinks: {item}")
        if item.is_file():
            records.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "sha256": file_sha256(item),
                }
            )
    return sha256_bytes(canonical_json_bytes(records))


def _included_paths(source_model: Path) -> list[str]:
    paths = list(_STATIC_INCLUDED)
    paths.extend(
        item.relative_to(source_model).as_posix()
        for item in sorted(
            (source_model / "definition" / "tables").glob("*.tmdl"),
            key=lambda candidate: candidate.name.casefold(),
        )
    )
    paths.extend(
        item.relative_to(source_model).as_posix()
        for item in sorted(
            (source_model / "definition" / "cultures").glob("*.tmdl"),
            key=lambda candidate: candidate.name.casefold(),
        )
    )
    result = sorted(set(paths))
    missing = [path for path in _STATIC_INCLUDED if not (source_model / path).is_file()]
    if missing:
        raise FixtureError("Required Power BI fixture files are missing: " + ", ".join(missing))
    if len([path for path in result if path.startswith("definition/tables/")]) != 9:
        raise FixtureError("The reviewed fixture must contain exactly nine table TMDL files.")
    if not any(path.startswith("definition/cultures/") for path in result):
        raise FixtureError("The reviewed fixture must contain its required culture TMDL.")
    return result


def _sanitize_tmdl(text: str, *, relative_path: str) -> tuple[str, set[str]]:
    replacements: dict[str, str] = {}

    def source_replacement(match: re.Match[str]) -> str:
        replacements[match.group("account")] = "SANITIZED_ACCOUNT"
        replacements[match.group("warehouse")] = "SANITIZED_WAREHOUSE"
        return 'Snowflake.Databases("SANITIZED_ACCOUNT","SANITIZED_WAREHOUSE",'

    def navigation_replacement(match: re.Match[str]) -> str:
        kind = match.group("kind")
        placeholder = "SANITIZED_DATABASE" if kind.casefold() == "database" else "SANITIZED_SCHEMA"
        replacements[match.group("name")] = placeholder
        return f'Name="{placeholder}",Kind="{kind}"'

    sanitized, source_count = _SNOWFLAKE_SOURCE.subn(source_replacement, text)
    sanitized, navigation_count = _NAVIGATION.subn(navigation_replacement, sanitized)
    for source_value, placeholder in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if source_value and source_value not in {
            "SANITIZED_ACCOUNT",
            "SANITIZED_WAREHOUSE",
            "SANITIZED_DATABASE",
            "SANITIZED_SCHEMA",
        }:
            sanitized = sanitized.replace(source_value, placeholder)
    if "Snowflake.Databases(" in text and source_count != 1:
        raise FixtureError(
            f"Expected exactly one supported Snowflake source in {relative_path}; found {source_count}."
        )
    if "Snowflake.Databases(" in text and navigation_count != 2:
        raise FixtureError(
            f"Expected Database and Schema navigation in {relative_path}; found {navigation_count}."
        )
    if "Snowflake.Databases(" in sanitized and (
        '"SANITIZED_ACCOUNT","SANITIZED_WAREHOUSE"' not in sanitized
        or 'Name="SANITIZED_DATABASE",Kind="Database"' not in sanitized
        or 'Name="SANITIZED_SCHEMA",Kind="Schema"' not in sanitized
    ):
        raise FixtureError(f"Snowflake locations were not fully sanitized in {relative_path}.")
    return sanitized, {value for value in replacements if value}


def sanitized_file_bytes(source: Path, relative_path: str) -> tuple[bytes, set[str]]:
    payload = source.read_bytes()
    if source.suffix.casefold() != ".tmdl":
        return payload, set()
    text = payload.decode("utf-8-sig")
    sanitized, removed = _sanitize_tmdl(text, relative_path=relative_path)
    return sanitized.replace("\r\n", "\n").encode("utf-8"), removed


def _write_sanitized_model(source_model: Path, target_model: Path) -> tuple[list[str], set[str]]:
    included = _included_paths(source_model)
    removed: set[str] = set()
    if target_model.exists():
        shutil.rmtree(target_model)
    for relative in included:
        source = source_model / relative
        if source.is_symlink():
            raise FixtureError(f"Source fixture inputs cannot be symlinks: {relative}")
        payload, values = sanitized_file_bytes(source, relative)
        removed.update(values)
        destination = target_model / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return included, removed


def _definition_tree_sha256(inventory: PowerBIModelInventory) -> str:
    return inventory.model.source_tree_hash


def _inventory_counts(inventory: PowerBIModelInventory) -> dict[str, int]:
    return {
        "columns": len(inventory.columns),
        "measures": len(inventory.measures),
        "relationships": len(inventory.relationships),
        "tables": len(inventory.tables),
    }


def _normalized_inventory(
    model: Path, repository_root: Path
) -> tuple[PowerBIModelInventory, str]:
    with tempfile.TemporaryDirectory(prefix="pbi-trial-normalized-") as temporary:
        root = Path(temporary)
        normalized_model = root / "pbi_trial.SemanticModel"
        _write_sanitized_model(model, normalized_model)
        inventory = extract_powerbi_inventory(normalized_model, root)
        return inventory, powerbi_structure_sha256(inventory)


def _manifest(
    *,
    source_inventory: PowerBIModelInventory,
    fixture_inventory: PowerBIModelInventory,
    fixture_model: Path,
    included_paths: Iterable[str],
    source_normalized_sha256: str,
    fixture_normalized_sha256: str,
) -> dict[str, Any]:
    fixture_structure = powerbi_structure_sha256(fixture_inventory)
    return {
        "connection_normalizer_version": CONNECTION_NORMALIZER_VERSION,
        "connection_normalized_semantic_structure_sha256": fixture_normalized_sha256,
        "excluded_content_classes": EXCLUDED_CONTENT_CLASSES,
        "fixture_definition_tree_sha256": _definition_tree_sha256(fixture_inventory),
        "fixture_inventory_semantic_sha256": fixture_inventory.semantic_hash,
        "fixture_model_tree_sha256": canonical_tree_sha256(fixture_model),
        "fixture_raw_structure_sha256": fixture_structure,
        "included_paths": sorted(included_paths),
        "inventory_counts": _inventory_counts(fixture_inventory),
        "prior_reviewed_raw_structure_sha256": REVIEWED_RAW_STRUCTURE_SHA256,
        "prior_reviewed_source_tree_sha256": REVIEWED_SOURCE_TREE_SHA256,
        "purpose": "PORTABLE_OFFLINE_PBI_TRIAL_V2_CONVERSION_AUDIT_FIXTURE",
        "sanitized": True,
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source_connection_normalized_semantic_structure_sha256": source_normalized_sha256,
    }


def build_fixture(
    *,
    source_model: Path,
    fixture_model: Path,
    manifest_path: Path,
    repository_root: Path,
    check: bool = False,
) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    source_resolved = source_model.resolve(strict=True)
    fixture_resolved = fixture_model.resolve(strict=False)
    manifest_resolved = manifest_path.resolve(strict=False)
    if (
        not fixture_resolved.is_relative_to(root)
        or not manifest_resolved.is_relative_to(root)
        or fixture_resolved == root
        or fixture_resolved == source_resolved
    ):
        raise FixtureError("Fixture outputs must be controlled paths inside the repository.")
    source_inventory = extract_powerbi_inventory(source_model, repository_root)
    if source_inventory.model.source_tree_hash != REVIEWED_SOURCE_TREE_SHA256:
        raise FixtureError("The ignored Power BI source tree differs from the reviewed audit input.")
    if powerbi_structure_sha256(source_inventory) != REVIEWED_RAW_STRUCTURE_SHA256:
        raise FixtureError("The ignored Power BI source structure differs from the reviewed profile.")

    with tempfile.TemporaryDirectory(prefix="pbi-trial-fixture-") as temporary:
        candidate = Path(temporary) / fixture_model.name
        included, removed = _write_sanitized_model(source_model, candidate)
        candidate_inventory = extract_powerbi_inventory(candidate, Path(temporary))
        source_normalized_inventory, source_normalized = _normalized_inventory(
            source_model, repository_root
        )
        candidate_normalized_inventory, candidate_normalized = _normalized_inventory(
            candidate, Path(temporary)
        )
        if source_normalized != candidate_normalized:
            raise FixtureError("Connection-normalized source and fixture structures differ.")
        if _inventory_counts(source_inventory) != EXPECTED_COUNTS:
            raise FixtureError("The reviewed source inventory counts changed.")
        if _inventory_counts(candidate_inventory) != EXPECTED_COUNTS:
            raise FixtureError("The sanitized fixture inventory counts changed.")
        if source_normalized_inventory.semantic_hash != candidate_normalized_inventory.semantic_hash:
            raise FixtureError("The connection-normalized semantic inventories differ.")
        manifest = _manifest(
            source_inventory=source_inventory,
            fixture_inventory=candidate_inventory,
            fixture_model=candidate,
            included_paths=included,
            source_normalized_sha256=source_normalized,
            fixture_normalized_sha256=candidate_normalized,
        )
        candidate_files = {
            item.relative_to(candidate).as_posix(): item.read_bytes()
            for item in candidate.rglob("*")
            if item.is_file()
        }
        for removed_value in removed:
            encoded = removed_value.encode("utf-8")
            if any(encoded in payload for payload in candidate_files.values()):
                raise FixtureError("A source connection literal survived fixture sanitization.")

        expected_manifest = (
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if check:
            actual_files = (
                {
                    item.relative_to(fixture_model).as_posix(): item.read_bytes()
                    for item in fixture_model.rglob("*")
                    if item.is_file()
                }
                if fixture_model.is_dir()
                else {}
            )
            if actual_files != candidate_files or not manifest_path.is_file():
                raise FixtureError("The committed portable fixture is missing or stale.")
            if manifest_path.read_bytes() != expected_manifest:
                raise FixtureError("The committed fixture manifest is stale.")
        else:
            if fixture_model.exists():
                shutil.rmtree(fixture_model)
            fixture_model.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidate, fixture_model)
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(expected_manifest)
        return manifest


def validate_committed_fixture(
    *,
    fixture_model: Path,
    manifest_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    if not fixture_model.is_dir() or not manifest_path.is_file():
        raise FixtureError("The portable Power BI fixture or manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {
        "connection_normalizer_version",
        "connection_normalized_semantic_structure_sha256",
        "excluded_content_classes",
        "fixture_definition_tree_sha256",
        "fixture_inventory_semantic_sha256",
        "fixture_model_tree_sha256",
        "fixture_raw_structure_sha256",
        "included_paths",
        "inventory_counts",
        "prior_reviewed_raw_structure_sha256",
        "prior_reviewed_source_tree_sha256",
        "purpose",
        "sanitized",
        "schema_version",
        "source_connection_normalized_semantic_structure_sha256",
    }:
        raise FixtureError("The fixture manifest has an invalid field set.")
    if manifest["schema_version"] != 1 or manifest["sanitized"] is not True:
        raise FixtureError("The fixture manifest version or sanitization state is invalid.")
    actual_paths = sorted(
        item.relative_to(fixture_model).as_posix()
        for item in fixture_model.rglob("*")
        if item.is_file()
    )
    if actual_paths != manifest["included_paths"]:
        raise FixtureError("The fixture file allowlist differs from the manifest.")
    inventory = extract_powerbi_inventory(fixture_model, repository_root)
    if _inventory_counts(inventory) != EXPECTED_COUNTS:
        raise FixtureError("The committed fixture inventory counts changed.")
    checks = {
        "fixture_definition_tree_sha256": inventory.model.source_tree_hash,
        "fixture_inventory_semantic_sha256": inventory.semantic_hash,
        "fixture_model_tree_sha256": canonical_tree_sha256(fixture_model),
        "fixture_raw_structure_sha256": powerbi_structure_sha256(inventory),
    }
    for key, value in checks.items():
        if manifest[key] != value:
            raise FixtureError(f"The fixture manifest has a stale {key}.")
    if (
        manifest["connection_normalized_semantic_structure_sha256"]
        != manifest["source_connection_normalized_semantic_structure_sha256"]
        or manifest["fixture_raw_structure_sha256"]
        != manifest["connection_normalized_semantic_structure_sha256"]
    ):
        raise FixtureError("The fixture normalized-structure evidence is inconsistent.")
    for item in fixture_model.rglob("*.tmdl"):
        text = item.read_text(encoding="utf-8")
        if "Snowflake.Databases(" in text and (
            '"SANITIZED_ACCOUNT","SANITIZED_WAREHOUSE"' not in text
            or 'Name="SANITIZED_DATABASE",Kind="Database"' not in text
            or 'Name="SANITIZED_SCHEMA",Kind="Schema"' not in text
        ):
            raise FixtureError(f"Unsanitized Snowflake connection metadata in {item.name}.")
    return manifest
