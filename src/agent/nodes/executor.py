from __future__ import annotations


def execute_sql(sql: str, runner) -> dict:
    result = runner.execute(sql)
    return {"rows": result.rows, "error": result.error or "", "empty": result.empty, "bytes_estimate": result.bytes_estimate}
