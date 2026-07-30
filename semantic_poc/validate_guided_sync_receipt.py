from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, Mapping

import jsonschema


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "semantic_poc"
    / "demo"
    / "guided_sync"
    / "task-marker.schema.json"
)


class ReceiptInvalid(RuntimeError):
    """Receipt syntax, schema, or path input is invalid."""


class ReceiptConflict(RuntimeError):
    """Receipt lifecycle state or a recomputed hash is stale."""


def canonical_json_bytes(value: Any, *, pretty: bool) -> bytes:
    if pretty:
        rendered = json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True
        )
    else:
        rendered = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    return (rendered + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, FileNotFoundError, OSError):
        return False


def repository_path(value: str, *, must_exist: bool = True) -> Path:
    if "\\" in value:
        raise ReceiptInvalid(f"Receipt paths must use POSIX separators: {value}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ReceiptInvalid(f"Receipt path is not a contained relative path: {value}")
    candidate = REPOSITORY_ROOT.joinpath(*pure.parts)
    cursor = REPOSITORY_ROOT
    for part in pure.parts:
        cursor = cursor / part
        if cursor.exists() and _is_link_or_reparse(cursor):
            raise ReceiptInvalid(f"Receipt paths cannot traverse links or junctions: {value}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise ReceiptInvalid(f"Receipt path escapes the repository: {value}")
    if must_exist and not candidate.exists():
        raise ReceiptConflict(f"Receipt path is missing: {value}")
    return candidate


def sha256_tree(path: Path, *, exclude: Path | None = None) -> str:
    if not path.is_dir():
        raise ReceiptConflict(f"Receipt TREE path is not a directory: {path}")
    records: list[dict[str, str]] = []
    def fail_enumeration(error: OSError) -> None:
        raise ReceiptConflict(
            f"Receipt TREE could not be enumerated: {path}: {error}"
        ) from error

    for root, directory_names, file_names in os.walk(
        path, topdown=True, onerror=fail_enumeration, followlinks=False
    ):
        root_path = Path(root)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            item = root_path / name
            if _is_link_or_reparse(item):
                raise ReceiptInvalid(
                    f"Receipt TREE contains a link or junction: {item}"
                )
        for name in file_names:
            item = root_path / name
            if (
                exclude is not None
                and item.resolve(strict=False) == exclude.resolve(strict=False)
            ):
                continue
            if _is_link_or_reparse(item):
                raise ReceiptInvalid(
                    f"Receipt TREE contains a link or junction: {item}"
                )
            records.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "sha256": sha256_file(item),
                }
            )
    records.sort(key=lambda item: item["path"])
    return sha256_bytes(canonical_json_bytes(records, pretty=False))


def hash_path(kind: str, path: Path, *, receipt_path: Path | None = None) -> str:
    if kind == "FILE":
        if not path.is_file():
            raise ReceiptConflict(f"Receipt FILE path is not a file: {path}")
        return sha256_file(path)
    if kind == "TREE":
        return sha256_tree(path, exclude=receipt_path)
    raise ReceiptInvalid(f"Unknown receipt path kind: {kind}")


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptInvalid(f"Receipt is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptInvalid("Receipt root must be an object.")
    if raw != canonical_json_bytes(value, pretty=True):
        raise ReceiptInvalid(
            "Receipt serialization must use UTF-8, LF, sorted keys, two-space indentation, and one trailing newline."
        )
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(value)
    except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
        raise ReceiptInvalid(f"Receipt schema validation failed: {exc}") from exc
    return value


def _validate_lifecycle(receipt: Mapping[str, Any]) -> None:
    limitations = {
        item["reason_code"]: item for item in receipt["limitations"]
    }
    if any(item["blocking"] for item in receipt["limitations"]):
        raise ReceiptConflict("Receipt contains a blocking limitation.")
    for item in receipt["validations"]:
        if item["required"]:
            if item["outcome"] != "PASSED" or item["exit_code"] != 0:
                raise ReceiptConflict("A required receipt validation did not pass.")
        elif item["outcome"] == "KNOWN_BASELINE_FAILURE":
            limitation = limitations.get(item["reason_code"])
            if item["exit_code"] == 0 or limitation is None or limitation["blocking"]:
                raise ReceiptConflict(
                    "A known baseline failure lacks a matching non-blocking limitation."
                )
        elif item["outcome"] == "PASSED" and item["exit_code"] != 0:
            raise ReceiptConflict("A PASSED validation must have exit code zero.")

    safety = receipt["safety"]
    task_id = receipt["task_id"]
    marker = receipt["terminal_marker"]
    if task_id != "06_OPTIONAL_LIVE_VALIDATION_EXPERIMENT":
        if any(safety.values()):
            raise ReceiptConflict("Tasks 01-05 require every safety flag to be false.")
    elif marker == "LIVE_VALIDATION_COMPLETE":
        if safety["network_contacted"] is not True:
            raise ReceiptConflict("Completed live validation must record network contact.")
        if any(value for key, value in safety.items() if key != "network_contacted"):
            raise ReceiptConflict("Live validation recorded a forbidden lifecycle action.")
    elif any(value for key, value in safety.items() if key != "network_contacted"):
        raise ReceiptConflict("Task 06 NOT_RUN recorded a forbidden lifecycle action.")


def _load_receipt_graph(
    receipt: Mapping[str, Any],
    receipt_path: Path,
) -> tuple[
    dict[Path, Mapping[str, Any]],
    dict[Path, tuple[Path, ...]],
    tuple[Path, ...],
]:
    root_path = receipt_path.resolve(strict=True)
    receipts: dict[Path, Mapping[str, Any]] = {root_path: receipt}
    prerequisites: dict[Path, tuple[Path, ...]] = {}
    task_paths: dict[str, Path] = {}
    visiting: set[Path] = set()
    visited: set[Path] = set()
    ordered: list[Path] = []

    def visit(path: Path) -> None:
        if path in visiting:
            raise ReceiptConflict("Prerequisite receipt graph contains a cycle.")
        if path in visited:
            return
        visiting.add(path)
        current = receipts[path]
        _validate_lifecycle(current)
        task_id = current["task_id"]
        previous_path = task_paths.setdefault(task_id, path)
        if previous_path != path:
            raise ReceiptConflict(
                f"Prerequisite receipt graph has ambiguous task ID: {task_id}"
            )

        child_paths: list[Path] = []
        seen_children: set[Path] = set()
        for item in current["prerequisite_receipts"]:
            child = repository_path(item["path"]).resolve(strict=True)
            if child in visiting:
                raise ReceiptConflict(
                    "Prerequisite receipt graph contains a cycle."
                )
            if child in seen_children:
                raise ReceiptConflict(
                    f"Prerequisite receipt is listed more than once: {item['path']}"
                )
            seen_children.add(child)
            if sha256_file(child) != item["sha256"]:
                raise ReceiptConflict(
                    f"Prerequisite receipt is stale: {item['path']}"
                )
            prerequisite = load_receipt(child)
            if (
                prerequisite["task_id"] != item["task_id"]
                or prerequisite["terminal_marker"] != item["terminal_marker"]
            ):
                raise ReceiptConflict(
                    f"Prerequisite task or marker differs: {item['path']}"
                )
            receipts.setdefault(child, prerequisite)
            child_paths.append(child)
            visit(child)
        prerequisites[path] = tuple(child_paths)
        visiting.remove(path)
        visited.add(path)
        ordered.append(path)

    visit(root_path)
    return receipts, prerequisites, tuple(ordered)


def _validate_recomputed(receipt: Mapping[str, Any], receipt_path: Path) -> None:
    receipts, prerequisites, ordered = _load_receipt_graph(
        receipt, receipt_path
    )
    ancestors: dict[Path, set[Path]] = {}
    for path in ordered:
        values: set[Path] = set(prerequisites[path])
        for prerequisite in prerequisites[path]:
            values.update(ancestors[prerequisite])
        ancestors[path] = values

    protected: dict[Path, tuple[str, str, str]] = {}
    artifact_bindings: dict[
        Path, list[tuple[Path, Mapping[str, Any]]]
    ] = {}
    for node_path in ordered:
        current = receipts[node_path]
        seen_protected: set[Path] = set()
        for item in current["protected_inputs"]:
            if item["before_sha256"] != item["after_sha256"]:
                raise ReceiptConflict(
                    f"Protected input changed during the task: {item['path']}"
                )
            path = repository_path(item["path"]).resolve(strict=True)
            if path in seen_protected:
                raise ReceiptConflict(
                    f"Protected input is listed more than once: {item['path']}"
                )
            seen_protected.add(path)
            binding = (item["kind"], item["after_sha256"], item["path"])
            previous = protected.setdefault(path, binding)
            if previous[:2] != binding[:2]:
                raise ReceiptConflict(
                    f"Protected input contract conflicts across receipts: "
                    f"{item['path']}"
                )
            actual = hash_path(
                item["kind"], path, receipt_path=node_path
            )
            if actual != item["after_sha256"]:
                raise ReceiptConflict(
                    f"Protected input is stale: {item['path']} "
                    f"(expected {item['after_sha256']}, actual {actual})"
                )

        seen_artifacts: set[Path] = set()
        for item in current["artifacts"]:
            path = repository_path(item["path"]).resolve(strict=True)
            if path in seen_artifacts:
                raise ReceiptConflict(
                    f"Receipt artifact is listed more than once: {item['path']}"
                )
            seen_artifacts.add(path)
            artifact_bindings.setdefault(path, []).append((node_path, item))

    for path, bindings in artifact_bindings.items():
        kinds = {item["kind"] for _, item in bindings}
        display_path = bindings[-1][1]["path"]
        if len(kinds) != 1:
            raise ReceiptConflict(
                f"Receipt artifact kind conflicts across receipts: {display_path}"
            )
        if path in protected:
            protected_kind, protected_sha256, protected_display = protected[path]
            if (
                kinds != {protected_kind}
                or any(
                    item["sha256"] != protected_sha256
                    for _, item in bindings
                )
            ):
                raise ReceiptConflict(
                    "A later receipt cannot supersede protected input: "
                    f"{protected_display}"
                )

        binding_nodes = [node for node, _ in bindings]
        for index, left in enumerate(binding_nodes):
            for right in binding_nodes[index + 1 :]:
                if (
                    left != right
                    and left not in ancestors[right]
                    and right not in ancestors[left]
                ):
                    raise ReceiptConflict(
                        "Receipt artifact lineage is ambiguous across branches: "
                        f"{display_path}"
                    )
        latest = [
            binding
            for binding in bindings
            if not any(
                binding[0] in ancestors[other[0]]
                for other in bindings
                if other[0] != binding[0]
            )
        ]
        if len(latest) != 1:
            raise ReceiptConflict(
                f"Receipt artifact lineage has no unique successor: {display_path}"
            )
        latest_node, latest_item = latest[0]
        actual = hash_path(
            latest_item["kind"], path, receipt_path=latest_node
        )
        if actual != latest_item["sha256"]:
            raise ReceiptConflict(
                f"Receipt artifact is stale: {latest_item['path']} "
                f"(expected {latest_item['sha256']}, actual {actual})"
            )


def validate_receipt(
    receipt_path: Path,
    *,
    expected_task: str,
    expected_marker: str,
    recompute: bool,
) -> dict[str, Any]:
    receipt_path = receipt_path.resolve(strict=True)
    if not receipt_path.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise ReceiptInvalid("Receipt must be inside the repository.")
    receipt = load_receipt(receipt_path)
    if receipt["task_id"] != expected_task:
        raise ReceiptConflict("Receipt task ID differs from --expected-task.")
    if receipt["terminal_marker"] != expected_marker:
        raise ReceiptConflict("Receipt marker differs from --expected-marker.")
    _validate_lifecycle(receipt)
    if recompute:
        _validate_recomputed(receipt, receipt_path)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a durable guided-sync task receipt."
    )
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--expected-task", required=True)
    parser.add_argument("--expected-marker", required=True)
    parser.add_argument("--recompute", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        requested = Path(args.receipt)
        if not requested.is_absolute():
            requested = REPOSITORY_ROOT / requested
        receipt = validate_receipt(
            requested,
            expected_task=args.expected_task,
            expected_marker=args.expected_marker,
            recompute=args.recompute,
        )
    except ReceiptInvalid as exc:
        print(f"GUIDED_SYNC_RECEIPT_INVALID: {exc}", file=sys.stderr)
        return 2
    except (ReceiptConflict, FileNotFoundError, OSError) as exc:
        print(f"GUIDED_SYNC_RECEIPT_CONFLICT: {exc}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "recomputed": args.recompute,
                "status": receipt["status"],
                "task_id": receipt["task_id"],
                "terminal_marker": receipt["terminal_marker"],
                "valid": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
