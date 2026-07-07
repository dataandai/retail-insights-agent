"""AST-based SQL validation for BigQuery SELECT-only analytics queries.

This module is deliberately deterministic: user input and LLM output are treated as data,
then validated before BigQuery ever sees the query. Read-only credentials protect the
warehouse; these guardrails protect the agent control plane and the user's budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Set

import sqlglot
from sqlglot import exp

from src.security.pii_patterns import BASE_PII_COLUMN_RE, runtime_pii_columns

ALLOWED_PROJECT = "bigquery-public-data"
ALLOWED_DATASET = "thelook_ecommerce"
ALLOWED_TABLES = {"orders", "order_items", "products", "users", "distribution_centers"}
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge, exp.Command)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""
    normalized_sql: str | None = None
    tables: frozenset[str] = frozenset()


def _reject(reason: str) -> ValidationResult:
    return ValidationResult(ok=False, reason=reason)


def _clean_sql(sql: str) -> str:
    text = (sql or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _table_leaf_name(table: exp.Table) -> str:
    return (table.name or "").strip("`")


def _identifier_value(value: str | None) -> str:
    return (value or "").strip("`")


def _table_is_from_allowed_dataset(table: exp.Table) -> bool:
    db = _identifier_value(table.db)
    catalog = _identifier_value(table.catalog)
    # Bare tables are allowed so generated SQL can be normalized by the validator and
    # unit-tested without requiring fully-qualified project names. Fully-qualified tables
    # must point to the intended public dataset.
    if db and db != ALLOWED_DATASET:
        return False
    if catalog and catalog != ALLOWED_PROJECT:
        return False
    return True


def extract_tables(sql: str) -> Set[str]:
    cleaned = _clean_sql(sql)
    statements = sqlglot.parse(cleaned, dialect="bigquery")
    tables: Set[str] = set()
    for stmt in statements:
        for table in stmt.find_all(exp.Table):
            tables.add(_table_leaf_name(table))
    return tables


def _projection_is_wildcard_projection(projection: exp.Expression) -> bool:
    """True for SELECT * / SELECT alias.* style user-facing projections.

    COUNT(*) is intentionally allowed because its top-level projection is a Count
    expression, not a materialized wildcard row projection. Raw wildcard
    projections are blocked because they can silently materialize PII columns such
    as users.email and users.street_address while evading name-based projection
    checks in the AST.
    """
    target = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(target, exp.Star):
        return True
    if isinstance(target, exp.Column) and _identifier_value(target.name) == "*":
        return True
    return False


def _select_projects_wildcard(stmt: exp.Select) -> bool:
    return any(_projection_is_wildcard_projection(projection) for projection in stmt.expressions)


def _select_projects_pii(stmt: exp.Select) -> list[str]:
    """Return PII columns that appear in user-facing SELECT projections.

    BigQuery is still allowed to touch PII columns for joins/filters, but generated
    analytics SQL must not materialize PII columns into result rows. This prevents
    large raw PII payloads from ever reaching the Python masking node and keeps the
    PII guard as a defense-in-depth layer rather than the first line of defense.
    """
    configured = runtime_pii_columns()
    offenders: set[str] = set()
    for projection in stmt.expressions:
        for col in projection.find_all(exp.Column):
            name = _identifier_value(col.name).lower()
            if name in configured or BASE_PII_COLUMN_RE.search(name):
                offenders.add(name)
    return sorted(offenders)


def validate_sql(sql: str) -> ValidationResult:
    cleaned = _clean_sql(sql)
    if not cleaned:
        return _reject("empty SQL")
    # The spec requires one statement and no multi-statement chaining. Reject semicolons
    # instead of trying to infer whether one is harmless.
    if ";" in cleaned:
        return _reject("semicolons are not allowed; exactly one SELECT statement is required")
    try:
        statements = sqlglot.parse(cleaned, dialect="bigquery")
    except Exception as exc:
        return _reject(f"SQL parse error: {exc}")

    if len(statements) != 1:
        return _reject("exactly one statement is required")
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        return _reject("only SELECT is allowed")
    if any(isinstance(node, FORBIDDEN) for node in stmt.walk()):
        return _reject("statement contains a disallowed operation")

    tables = {_table_leaf_name(t) for t in stmt.find_all(exp.Table)}
    if not tables:
        return _reject("analytics queries must reference at least one allow-listed table")
    unknown = tables - ALLOWED_TABLES
    if unknown:
        return _reject(f"references non-allowlisted tables: {sorted(unknown)}")
    bad_dataset = [t.sql(dialect="bigquery") for t in stmt.find_all(exp.Table) if not _table_is_from_allowed_dataset(t)]
    if bad_dataset:
        return _reject(f"references tables outside {ALLOWED_PROJECT}.{ALLOWED_DATASET}: {bad_dataset}")

    if _select_projects_wildcard(stmt):
        return _reject("wildcard SELECT projections are not allowed; explicitly select non-PII analytical columns")

    pii_projected = _select_projects_pii(stmt)
    if pii_projected:
        return _reject(f"PII columns must not be projected in SELECT output: {pii_projected}")

    normalized = stmt.sql(dialect="bigquery")
    return ValidationResult(ok=True, normalized_sql=normalized, tables=frozenset(tables))
