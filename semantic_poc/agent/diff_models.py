from __future__ import annotations

import difflib
import json
from typing import Any, Mapping, Sequence


def stable_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def text_diff(current: str | None, proposed: str | None, *, current_name: str, proposed_name: str) -> str:
    current_text = "" if current is None else current.rstrip() + "\n"
    proposed_text = "" if proposed is None else proposed.rstrip() + "\n"
    if current_text == proposed_text:
        return ""
    return "".join(
        difflib.unified_diff(
            current_text.splitlines(keepends=True),
            proposed_text.splitlines(keepends=True),
            fromfile=current_name,
            tofile=proposed_name,
            lineterm="\n",
        )
    )


def definition_diff(
    current: Mapping[str, Any] | None,
    proposed: Mapping[str, Any] | None,
    *,
    current_name: str,
    proposed_name: str,
) -> str:
    current_text = None if current is None else stable_json(current)
    proposed_text = None if proposed is None else stable_json(proposed)
    return text_diff(current_text, proposed_text, current_name=current_name, proposed_name=proposed_name)


def canonical_pseudo_diff(operations: Sequence[Mapping[str, Any]]) -> str:
    if not operations:
        return ""
    lines = ["--- current/canonical", "+++ proposed/canonical"]
    for operation in operations:
        if operation.get("operation") == "insert_typed_metric":
            name = operation.get("metric_name")
            lines.append(f"@@ metrics[{name}] @@")
            lines.append("+ " + _inline(operation.get("definition")))
            continue
        selector = operation["selector"]
        lines.append(f"@@ {selector} @@")
        lines.append("- " + _inline(operation.get("current")))
        lines.append("+ " + _inline(operation.get("proposed")))
    return "\n".join(lines) + "\n"


def _inline(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
