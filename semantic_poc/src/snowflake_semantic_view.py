from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

import yaml

from .models import (
    CANONICAL_SOURCE,
    GENERATED_NOTICE,
    OUTPUT_DIR,
    REQUIRED_PUBLIC_METRICS,
    SNOWFLAKE_ENVIRONMENT,
    SNOWFLAKE_OUTPUT,
    SNOWFLAKE_VERIFICATION_JSON,
    SNOWFLAKE_VERIFICATION_MD,
    load_yaml,
    normalize_snowflake_environment,
    relative_posix,
    write_json,
)


VERIFY_SQL = "CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(%s, %s, %s)"
PLACEHOLDER_PATTERNS = [
    re.compile(r"<[^>\n]+>"),
    re.compile(r"\{\{.*?\}\}"),
    re.compile(r"\b(TODO|TBD|PLACEHOLDER|REPLACE_ME|YOUR_[A-Z0-9_]+)\b", re.IGNORECASE),
]
SECRET_KEY_PARTS = ("password", "pwd", "token", "secret", "private_key")
STANDARD_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SQL_FUNCTIONS_AND_KEYWORDS = {
    "and",
    "as",
    "asc",
    "avg",
    "by",
    "case",
    "count",
    "count_if",
    "desc",
    "distinct",
    "div0",
    "else",
    "end",
    "excluding",
    "false",
    "first",
    "if",
    "iff",
    "is",
    "last",
    "max",
    "metrics",
    "min",
    "not",
    "null",
    "nullif",
    "nulls",
    "or",
    "order",
    "over",
    "partition",
    "sum",
    "then",
    "true",
    "when",
}


class SnowflakeSemanticViewError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedSemanticYaml:
    path: Path
    text: str
    data: dict[str, Any]


@dataclass(frozen=True)
class CommandOutcome:
    return_code: int
    report: dict[str, Any]
    json_path: Path
    markdown_path: Path


def read_semantic_view_yaml(path: Path) -> LoadedSemanticYaml:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise SnowflakeSemanticViewError(f"Snowflake Semantic View YAML must be a mapping: {path}")
    return LoadedSemanticYaml(path=path, text=text, data=data)


def clean_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('""', '"')
    return text


def quote_identifier(value: Any) -> str:
    return '"' + clean_identifier(value).replace('"', '""') + '"'


def schema_string_component(value: Any) -> str:
    clean = clean_identifier(value)
    if STANDARD_IDENTIFIER.match(clean):
        return clean
    return quote_identifier(clean)


def target_schema_name(environment: dict[str, Any]) -> str:
    normalized = normalize_snowflake_environment(environment)
    return ".".join(
        [
            schema_string_component(normalized.get("database")),
            schema_string_component(normalized.get("semantic_schema")),
        ]
    )


def semantic_view_name(environment: dict[str, Any]) -> str:
    normalized = normalize_snowflake_environment(environment)
    return ".".join(
        [
            schema_string_component(normalized.get("database")),
            schema_string_component(normalized.get("semantic_schema")),
            schema_string_component(normalized.get("semantic_view_name")),
        ]
    )


def quote_full_name(*parts: Any) -> str:
    return ".".join(quote_identifier(part) for part in parts)


def read_snowflake_environment(path: Path) -> dict[str, Any]:
    return normalize_snowflake_environment(load_yaml(path) or {})


def target_info(environment: dict[str, Any], *, connection_name: str | None = None) -> dict[str, Any]:
    normalized = normalize_snowflake_environment(environment)
    selected_connection = connection_name or normalized.get("connection_name") or os.getenv("SNOWFLAKE_CONNECTION_NAME")
    account = os.getenv("SNOWFLAKE_ACCOUNT")
    if account:
        account_label = account
    elif selected_connection:
        account_label = f"connection:{selected_connection}"
    elif os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME"):
        account_label = f"default_connection:{os.getenv('SNOWFLAKE_DEFAULT_CONNECTION_NAME')}"
    else:
        account_label = "default Snowflake connection"
    return {
        "account": account_label,
        "connection_name": selected_connection,
        "database": normalized.get("database"),
        "mart_schema": normalized.get("mart_schema"),
        "semantic_schema": normalized.get("semantic_schema"),
        "target_schema": target_schema_name(normalized),
        "semantic_view_name": normalized.get("semantic_view_name"),
        "semantic_view": semantic_view_name(normalized),
        "warehouse": normalized.get("warehouse") or os.getenv("SNOWFLAKE_WAREHOUSE"),
        "role": normalized.get("role") or os.getenv("SNOWFLAKE_ROLE"),
    }


def secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        if any(part in key.lower() for part in SECRET_KEY_PARTS) and value and len(value) >= 4:
            values.append(value)
    return sorted(values, key=len, reverse=True)


def redact_secrets(value: Any, extra_values: Sequence[str] | None = None) -> str:
    text = str(value)
    for secret in [*(extra_values or []), *secret_values()]:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def find_placeholders(yaml_text: str, yaml_data: dict[str, Any]) -> list[str]:
    matches: list[str] = []
    for pattern in PLACEHOLDER_PATTERNS:
        matches.extend(match.group(0) for match in pattern.finditer(yaml_text))
    for key in yaml_data:
        if any(part in str(key).lower() for part in SECRET_KEY_PARTS):
            matches.append(f"secret-like config key in YAML: {key}")
    return sorted(set(matches))


def physical_tables(yaml_data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for table in yaml_data.get("tables", []) or []:
        base = table.get("base_table", {}) if isinstance(table, dict) else {}
        database = base.get("database")
        schema = base.get("schema")
        table_name = base.get("table")
        if database and schema and table_name:
            names.append(f"{database}.{schema}.{table_name}")
    return names


def validate_non_secret_environment(environment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in environment:
        if any(part in str(key).lower() for part in SECRET_KEY_PARTS):
            errors.append(f"Snowflake environment config contains a secret-like key: {key}")
    return errors


def validate_required_environment(environment: dict[str, Any]) -> list[str]:
    required = ["database", "mart_schema", "semantic_schema", "semantic_view_name"]
    missing = [field for field in required if not environment.get(field)]
    if not missing:
        return []
    return ["Snowflake environment fields are missing: " + ", ".join(missing)]


def section_errors(yaml_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section in ("name", "tables", "relationships", "metrics"):
        if section not in yaml_data:
            errors.append(f"Snowflake YAML is missing required section: {section}")
    if "tables" in yaml_data and not isinstance(yaml_data.get("tables"), list):
        errors.append("Snowflake YAML section must be a list: tables")
    if "relationships" in yaml_data and not isinstance(yaml_data.get("relationships"), list):
        errors.append("Snowflake YAML section must be a list: relationships")
    if "metrics" in yaml_data and not isinstance(yaml_data.get("metrics"), list):
        errors.append("Snowflake YAML section must be a list: metrics")
    return errors


def base_table_errors(yaml_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for table in yaml_data.get("tables", []) or []:
        if not isinstance(table, dict):
            errors.append("Snowflake YAML tables must contain mappings.")
            continue
        table_name = table.get("name") or "<unnamed>"
        base = table.get("base_table")
        if not isinstance(base, dict):
            errors.append(f"Table {table_name} is missing base_table.")
            continue
        missing = [field for field in ("database", "schema", "table") if not base.get(field)]
        if missing:
            errors.append(f"Table {table_name} base_table is not fully qualified; missing {', '.join(missing)}.")
    return errors


def names_from_items(items: Sequence[dict[str, Any]] | None) -> set[str]:
    return {str(item.get("name")) for item in items or [] if isinstance(item, dict) and item.get("name")}


def table_definitions(yaml_data: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    definitions: dict[str, dict[str, set[str]]] = {}
    for table in yaml_data.get("tables", []) or []:
        if not isinstance(table, dict) or not table.get("name"):
            continue
        definitions[str(table["name"])] = {
            "facts": names_from_items(table.get("facts")),
            "metrics": names_from_items(table.get("metrics")),
            "dimensions": names_from_items(table.get("dimensions")) | names_from_items(table.get("time_dimensions")),
        }
    return definitions


def referenced_qualified_names(expression: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b", expression)]


def referenced_unqualified_names(expression: str) -> set[str]:
    expression_without_qualified = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", " ", expression)
    names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression_without_qualified))
    return {name for name in names if name.lower() not in SQL_FUNCTIONS_AND_KEYWORDS}


def validate_expression_references(
    expression: str,
    *,
    current_table: str | None,
    definitions: dict[str, dict[str, set[str]]],
    top_level_metrics: set[str],
) -> list[str]:
    errors: list[str] = []
    for table_name, object_name in referenced_qualified_names(expression):
        table = definitions.get(table_name)
        if table is None:
            errors.append(f"Expression references unknown logical table {table_name}: {expression}")
            continue
        known_names = table["facts"] | table["metrics"] | table["dimensions"]
        if object_name not in known_names:
            errors.append(f"Expression references undefined fact or metric {table_name}.{object_name}: {expression}")
    for object_name in referenced_unqualified_names(expression):
        if current_table is None:
            if object_name not in top_level_metrics:
                errors.append(f"Top-level metric expression references undefined metric {object_name}: {expression}")
            continue
        table = definitions.get(current_table, {})
        known_names = table.get("facts", set()) | table.get("metrics", set()) | table.get("dimensions", set())
        if object_name not in known_names:
            errors.append(f"Metric expression on {current_table} references undefined fact or metric {object_name}: {expression}")
    return errors


def reference_errors(yaml_data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    definitions = table_definitions(yaml_data)
    top_level_metrics = names_from_items(yaml_data.get("metrics"))
    for relationship in yaml_data.get("relationships", []) or []:
        if not isinstance(relationship, dict):
            errors.append("Snowflake YAML relationships must contain mappings.")
            continue
        for field in ("left_table", "right_table"):
            table_name = relationship.get(field)
            if table_name not in definitions:
                errors.append(f"Relationship {relationship.get('name') or '<unnamed>'} references unknown {field}: {table_name}")
        columns = relationship.get("relationship_columns")
        if not columns:
            errors.append(f"Relationship {relationship.get('name') or '<unnamed>'} is missing relationship_columns.")
    for table in yaml_data.get("tables", []) or []:
        if not isinstance(table, dict) or not table.get("name"):
            continue
        for metric in table.get("metrics", []) or []:
            expression = str(metric.get("expr") or "")
            errors.extend(
                validate_expression_references(
                    expression,
                    current_table=str(table["name"]),
                    definitions=definitions,
                    top_level_metrics=top_level_metrics,
                )
            )
    for metric in yaml_data.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        expression = str(metric.get("expr") or "")
        errors.extend(
            validate_expression_references(
                expression,
                current_table=None,
                definitions=definitions,
                top_level_metrics=top_level_metrics,
            )
        )
    return errors


def run_preflight(loaded_yaml: LoadedSemanticYaml, environment: dict[str, Any]) -> dict[str, Any]:
    placeholders = find_placeholders(loaded_yaml.text, loaded_yaml.data)
    errors = []
    errors.extend(validate_non_secret_environment(environment))
    errors.extend(validate_required_environment(environment))
    errors.extend(section_errors(loaded_yaml.data))
    errors.extend(base_table_errors(loaded_yaml.data))
    errors.extend(reference_errors(loaded_yaml.data))
    if placeholders:
        errors.append("Snowflake YAML contains unresolved placeholders: " + ", ".join(placeholders))
    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "physical_tables": physical_tables(loaded_yaml.data),
    }


def connection_kwargs(environment: dict[str, Any], *, connection_name: str | None = None) -> dict[str, Any]:
    selected_connection = connection_name or environment.get("connection_name") or os.getenv("SNOWFLAKE_CONNECTION_NAME")
    if selected_connection:
        return {"connection_name": selected_connection}
    if os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME"):
        return {"connection_name": os.getenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME")}

    env_mapping = {
        "SNOWFLAKE_ACCOUNT": "account",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "password",
        "SNOWFLAKE_AUTHENTICATOR": "authenticator",
        "SNOWFLAKE_PRIVATE_KEY_FILE": "private_key_file",
        "SNOWFLAKE_PRIVATE_KEY_FILE_PWD": "private_key_file_pwd",
    }
    kwargs = {target: os.getenv(source) for source, target in env_mapping.items() if os.getenv(source)}
    if kwargs:
        kwargs["database"] = environment.get("database")
        kwargs["schema"] = environment.get("semantic_schema")
        if environment.get("warehouse") or os.getenv("SNOWFLAKE_WAREHOUSE"):
            kwargs["warehouse"] = environment.get("warehouse") or os.getenv("SNOWFLAKE_WAREHOUSE")
        if environment.get("role") or os.getenv("SNOWFLAKE_ROLE"):
            kwargs["role"] = environment.get("role") or os.getenv("SNOWFLAKE_ROLE")
    return {key: value for key, value in kwargs.items() if value}


def load_connector_connect(connector: Any | None = None) -> Callable[..., Any]:
    if connector is not None:
        if callable(connector):
            return connector
        if hasattr(connector, "connect"):
            return connector.connect
        raise SnowflakeSemanticViewError("Injected Snowflake connector must be callable or expose connect().")
    try:
        import snowflake.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SnowflakeSemanticViewError(
            'Snowflake connector is not installed. Install it with `python -m pip install -e ".[snowflake]"` '
            "or configure an environment that already provides snowflake-connector-python."
        ) from exc
    return snowflake.connector.connect


def cursor_execute(cursor: Any, sql: str, params: Sequence[Any] | None = None) -> Any:
    if params is None:
        return cursor.execute(sql)
    return cursor.execute(sql, tuple(params))


def fetch_scalar(cursor: Any, sql: str, params: Sequence[Any] | None = None) -> Any:
    cursor_execute(cursor, sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def rows_as_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall() or []
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [{str(key).lower(): value for key, value in row.items()} for row in rows]
    description = getattr(cursor, "description", None) or []
    names = [str(item[0]).lower() for item in description]
    return [dict(zip(names, row)) for row in rows]


def configure_session(cursor: Any, environment: dict[str, Any]) -> None:
    role = environment.get("role") or os.getenv("SNOWFLAKE_ROLE")
    warehouse = environment.get("warehouse") or os.getenv("SNOWFLAKE_WAREHOUSE")
    if role:
        cursor_execute(cursor, f"USE ROLE {quote_identifier(role)}")
    if warehouse:
        cursor_execute(cursor, f"USE WAREHOUSE {quote_identifier(warehouse)}")
    if environment.get("database"):
        cursor_execute(cursor, f"USE DATABASE {quote_identifier(environment['database'])}")
    if environment.get("semantic_schema"):
        cursor_execute(cursor, f"USE SCHEMA {quote_identifier(environment['semantic_schema'])}")


def call_create_semantic_view_from_yaml(cursor: Any, target_schema: str, yaml_text: str, *, verify_only: bool) -> Any:
    return fetch_scalar(cursor, VERIFY_SQL, [target_schema, yaml_text, verify_only])


def run_smoke_tests(cursor: Any, environment: dict[str, Any]) -> dict[str, Any]:
    full_view_name = quote_full_name(
        environment["database"],
        environment["semantic_schema"],
        environment["semantic_view_name"],
    )
    smoke: dict[str, Any] = {
        "success": True,
        "semantic_view_exists": {"status": "not_run"},
        "metric_metadata": {"status": "not_run"},
        "queries": [],
    }

    try:
        cursor_execute(cursor, f"DESCRIBE SEMANTIC VIEW {full_view_name}")
        rows = rows_as_dicts(cursor)
        smoke["semantic_view_exists"] = {"status": "success", "rows": len(rows)}
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        smoke["success"] = False
        smoke["semantic_view_exists"] = {"status": "failed", "error": redact_secrets(exc)}

    try:
        cursor_execute(cursor, f"SHOW SEMANTIC METRICS IN {full_view_name}")
        metric_rows = rows_as_dicts(cursor)
        metric_names = sorted({str(row.get("name") or row.get("metric_name") or "") for row in metric_rows if row})
        missing = [metric for metric in REQUIRED_PUBLIC_METRICS if metric not in metric_names]
        smoke["metric_metadata"] = {
            "status": "success",
            "available_metrics": metric_names,
            "missing_public_metrics": missing,
        }
        if missing:
            smoke["success"] = False
    except Exception as exc:  # pragma: no cover - depends on Snowflake account features
        smoke["metric_metadata"] = {"status": "skipped", "error": redact_secrets(exc)}

    query_specs = [
        ("valid_sbr_finishers", f"SELECT * FROM SEMANTIC_VIEW({full_view_name} METRICS results.valid_sbr_finishers)"),
        ("event_context_rate", f"SELECT * FROM SEMANTIC_VIEW({full_view_name} METRICS event_context_rate)"),
    ]
    for name, sql in query_specs:
        item = {"name": name, "sql": sql, "status": "not_run"}
        try:
            cursor_execute(cursor, sql)
            sample = cursor.fetchone()
            item.update({"status": "success", "sample_row": repr(sample)})
        except Exception as exc:  # pragma: no cover - exercised by integration failures
            smoke["success"] = False
            item.update({"status": "failed", "error": redact_secrets(exc)})
        smoke["queries"].append(item)
    return smoke


def base_report(
    *,
    action: str,
    target: dict[str, Any],
    yaml_path: Path,
    environment_path: Path,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "target": target,
        "yaml_path": relative_posix(yaml_path),
        "environment_path": relative_posix(environment_path),
        "verification_status": "not_run",
        "snowflake_response": None,
        "physical_tables": (preflight or {}).get("physical_tables", []),
        "errors": [],
        "deployment_readiness": "unknown",
        "deployment_performed": False,
        "deployment_status": "not_requested",
        "deployment_response": None,
        "smoke_tests": {"success": None, "queries": []},
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Snowflake Semantic View Verification",
        "",
        GENERATED_NOTICE,
        "",
        f"- Target schema: `{report['target'].get('target_schema')}`",
        f"- Semantic view: `{report['target'].get('semantic_view')}`",
        f"- Account: `{report['target'].get('account')}`",
        f"- Verification status: `{report.get('verification_status')}`",
        f"- Deployment readiness: `{report.get('deployment_readiness')}`",
        f"- Deployment performed: `{report.get('deployment_performed')}`",
        f"- Deployment status: `{report.get('deployment_status')}`",
        "",
        "## Snowflake Response",
        "",
        str(report.get("snowflake_response") or report.get("deployment_response") or "None"),
        "",
        "## Physical Tables",
        "",
    ]
    tables = report.get("physical_tables") or []
    lines.extend([f"- `{table}`" for table in tables] if tables else ["- None."])
    lines.extend(["", "## Errors", ""])
    errors = report.get("errors") or []
    lines.extend([f"- {error}" for error in errors] if errors else ["- None."])
    lines.extend(["", "## Smoke Tests", ""])
    smoke = report.get("smoke_tests") or {}
    if smoke.get("success") is None:
        lines.append("- Not run.")
    else:
        lines.append(f"- Overall: `{smoke.get('success')}`")
        lines.append(f"- Semantic view exists: `{(smoke.get('semantic_view_exists') or {}).get('status')}`")
        metadata = smoke.get("metric_metadata") or {}
        lines.append(f"- Metric metadata: `{metadata.get('status')}`")
        missing = metadata.get("missing_public_metrics") or []
        if missing:
            lines.append("- Missing public metrics: " + ", ".join(f"`{metric}`" for metric in missing))
        for query in smoke.get("queries") or []:
            lines.append(f"- Query `{query.get('name')}`: `{query.get('status')}`")
    lines.append("")
    return "\n".join(lines)


def write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    json_path = output_dir / SNOWFLAKE_VERIFICATION_JSON.name
    markdown_path = output_dir / SNOWFLAKE_VERIFICATION_MD.name
    write_json(json_path, report, generated=True, canonical_source=CANONICAL_SOURCE)
    write_markdown(markdown_path, render_markdown_report(report))
    return json_path, markdown_path


def print_target(target: dict[str, Any], *, stream: TextIO) -> None:
    print("Snowflake semantic view target:", file=stream)
    print(f"  account: {target.get('account')}", file=stream)
    print(f"  database: {target.get('database')}", file=stream)
    print(f"  mart schema: {target.get('mart_schema')}", file=stream)
    print(f"  semantic schema: {target.get('semantic_schema')}", file=stream)
    print(f"  semantic view: {target.get('semantic_view_name')}", file=stream)
    if target.get("warehouse"):
        print(f"  warehouse: {target.get('warehouse')}", file=stream)
    if target.get("role"):
        print(f"  role: {target.get('role')}", file=stream)


def run_snowflake_semantic_view_command(
    *,
    action: str,
    yaml_path: Path = SNOWFLAKE_OUTPUT,
    environment_path: Path = SNOWFLAKE_ENVIRONMENT,
    output_dir: Path = OUTPUT_DIR,
    apply: bool = False,
    connector: Any | None = None,
    connection_name: str | None = None,
    stream: TextIO | None = None,
) -> CommandOutcome:
    stream = stream or sys.stdout
    output_dir = output_dir.resolve()
    try:
        environment = read_snowflake_environment(environment_path)
        if connection_name:
            environment["connection_name"] = connection_name
    except Exception as exc:
        environment = {}
        target = target_info(environment, connection_name=connection_name)
        report = base_report(action=action, target=target, yaml_path=yaml_path, environment_path=environment_path)
        report["verification_status"] = "preflight_failed"
        report["deployment_readiness"] = "not_ready"
        report["errors"] = [redact_secrets(exc)]
        json_path, markdown_path = write_reports(report, output_dir)
        print(redact_secrets(exc), file=stream)
        return CommandOutcome(1, report, json_path, markdown_path)
    target = target_info(environment, connection_name=connection_name)

    try:
        loaded_yaml = read_semantic_view_yaml(yaml_path)
    except Exception as exc:
        report = base_report(action=action, target=target, yaml_path=yaml_path, environment_path=environment_path)
        report["verification_status"] = "preflight_failed"
        report["deployment_readiness"] = "not_ready"
        report["errors"] = [redact_secrets(exc)]
        json_path, markdown_path = write_reports(report, output_dir)
        print(redact_secrets(exc), file=stream)
        return CommandOutcome(1, report, json_path, markdown_path)

    preflight = run_preflight(loaded_yaml, environment)
    report = base_report(
        action=action,
        target=target,
        yaml_path=yaml_path,
        environment_path=environment_path,
        preflight=preflight,
    )
    if preflight["errors"]:
        report["verification_status"] = "preflight_failed"
        report["deployment_readiness"] = "not_ready"
        report["errors"] = [redact_secrets(error) for error in preflight["errors"]]
        json_path, markdown_path = write_reports(report, output_dir)
        for error in report["errors"]:
            print(error, file=stream)
        return CommandOutcome(1, report, json_path, markdown_path)

    report["deployment_readiness"] = "ready"
    print_target(target, stream=stream)

    try:
        connect = load_connector_connect(connector)
        kwargs = connection_kwargs(environment, connection_name=connection_name)
        with connect(**kwargs) as connection:
            with connection.cursor() as cursor:
                configure_session(cursor, environment)
                response = call_create_semantic_view_from_yaml(
                    cursor,
                    report["target"]["target_schema"],
                    loaded_yaml.text,
                    verify_only=True,
                )
                report["verification_status"] = "succeeded"
                report["snowflake_response"] = redact_secrets(response)

                if action == "deploy":
                    if not apply:
                        report["deployment_status"] = "blocked_without_apply"
                        print("Deployment skipped because --apply was not provided.", file=stream)
                    else:
                        print("This will create or replace:", file=stream)
                        print(report["target"]["semantic_view"], file=stream)
                        deployment_response = call_create_semantic_view_from_yaml(
                            cursor,
                            report["target"]["target_schema"],
                            loaded_yaml.text,
                            verify_only=False,
                        )
                        report["deployment_performed"] = True
                        report["deployment_status"] = "succeeded"
                        report["deployment_response"] = redact_secrets(deployment_response)
                        report["smoke_tests"] = run_smoke_tests(cursor, environment)
                        if not report["smoke_tests"].get("success"):
                            report["errors"].append("Post-deployment smoke tests failed.")
    except Exception as exc:
        status_key = "deployment_status" if action == "deploy" and apply and report["verification_status"] == "succeeded" else "verification_status"
        report[status_key] = "failed"
        report["deployment_readiness"] = "not_ready" if report["verification_status"] != "succeeded" else report["deployment_readiness"]
        report["errors"].append(redact_secrets(exc))

    json_path, markdown_path = write_reports(report, output_dir)
    print(f"Wrote {relative_posix(json_path)}", file=stream)
    print(f"Wrote {relative_posix(markdown_path)}", file=stream)

    if report["errors"]:
        return CommandOutcome(1, report, json_path, markdown_path)
    return CommandOutcome(0, report, json_path, markdown_path)


def resolve_cli_path(value: str | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def build_parser(description: str, *, deploy: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--yaml", default=str(SNOWFLAKE_OUTPUT), help="Generated Snowflake Semantic View YAML path.")
    parser.add_argument(
        "--environment",
        default=str(SNOWFLAKE_ENVIRONMENT),
        help="Snowflake non-secret environment YAML path.",
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Directory for verification reports.")
    parser.add_argument("--connection-name", help="Named Snowflake connection profile to use.")
    if deploy:
        parser.add_argument("--apply", action="store_true", help="Create or replace the semantic view after verification.")
    return parser


def main_verify(argv: Sequence[str] | None = None) -> int:
    parser = build_parser("Verify generated Snowflake Semantic View YAML.", deploy=False)
    args = parser.parse_args(argv)
    outcome = run_snowflake_semantic_view_command(
        action="verify",
        yaml_path=resolve_cli_path(args.yaml, SNOWFLAKE_OUTPUT),
        environment_path=resolve_cli_path(args.environment, SNOWFLAKE_ENVIRONMENT),
        output_dir=resolve_cli_path(args.output_dir, OUTPUT_DIR),
        connection_name=args.connection_name,
    )
    return outcome.return_code


def main_deploy(argv: Sequence[str] | None = None) -> int:
    parser = build_parser("Verify and optionally deploy generated Snowflake Semantic View YAML.", deploy=True)
    args = parser.parse_args(argv)
    outcome = run_snowflake_semantic_view_command(
        action="deploy",
        yaml_path=resolve_cli_path(args.yaml, SNOWFLAKE_OUTPUT),
        environment_path=resolve_cli_path(args.environment, SNOWFLAKE_ENVIRONMENT),
        output_dir=resolve_cli_path(args.output_dir, OUTPUT_DIR),
        apply=args.apply,
        connection_name=args.connection_name,
    )
    return outcome.return_code
