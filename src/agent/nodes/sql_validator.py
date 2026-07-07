from __future__ import annotations

from src.security.sql_guardrails import validate_sql


def validate_generated_sql(sql: str) -> dict:
    result = validate_sql(sql)
    return {"validation_ok": result.ok, "validation_error": result.reason, "sql": result.normalized_sql or sql, "tables": sorted(result.tables)}
