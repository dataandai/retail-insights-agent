from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


from src.agent.graph import RetailInsightsAgent
from src.agent.nodes.self_healer import MAX_HEALING_ATTEMPTS
from src.database.bigquery_runner import MockBigQueryRunner, make_runner
from src.llm.client import DeterministicStubLLM
from src.database.reports_store import ReportsStore
from src.security.pii_patterns import EMAIL_RE, ISO_DATE_RE, contains_phone
from src.security.sql_guardrails import extract_tables, validate_sql


class _BlockedSocket:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network access is forbidden in evaluation --mode mock")


def _disable_network_for_mock() -> None:
    """Physically prevent accidental Gemini/BigQuery calls in mock eval mode."""
    socket.socket = _BlockedSocket  # type: ignore[assignment]


def has_pii(text: str) -> bool:
    without_dates = ISO_DATE_RE.sub("", text)
    return bool(EMAIL_RE.search(without_dates)) or contains_phone(without_dates)


def _normalize_eval_value(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value is None:
        return None
    return str(value)


def _row_sort_key(row: dict[str, Any]) -> str:
    normalized = {key: _normalize_eval_value(row.get(key)) for key in sorted(row)}
    return json.dumps(normalized, sort_keys=True, default=str)


def _values_equal(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) and not isinstance(left, bool) and not isinstance(right, bool):
        return abs(float(left) - float(right)) <= tolerance
    return _normalize_eval_value(left) == _normalize_eval_value(right)


def _rows_equal(left: dict[str, Any], right: dict[str, Any], tolerance: float) -> bool:
    if set(left.keys()) != set(right.keys()):
        return False
    return all(_values_equal(left[key], right[key], tolerance) for key in left.keys())


def compare_result_sets(agent_rows: list[dict[str, Any]], reference_rows: list[dict[str, Any]], tolerance: float = 1e-6) -> bool:
    """Compare execution results row-by-row, not just aggregate numeric totals.

    Earlier mock evals used numeric signatures that could pass if categorical labels were
    swapped (for example Texas and California revenue exchanged). This comparator keeps
    category/date/string columns in the assertion while still allowing small numeric
    tolerance for real BigQuery floating-point differences. Row order is ignored.
    """
    if len(agent_rows) != len(reference_rows):
        return False
    unmatched = sorted(reference_rows, key=_row_sort_key)
    for agent_row in sorted(agent_rows, key=_row_sort_key):
        match_index = next((i for i, ref_row in enumerate(unmatched) if _rows_equal(agent_row, ref_row, tolerance)), None)
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return not unmatched


def _reference_rows(case: dict[str, Any], runner, cache_dir: Path, *, refresh_cache: bool) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{case['id']}.json"
    if cache_path.exists() and not refresh_cache:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    ref_validation = validate_sql(case["reference_sql"])
    if not ref_validation.ok:
        raise ValueError(f"bad reference_sql for {case['id']}: {ref_validation.reason}")
    rows = runner.execute(ref_validation.normalized_sql or case["reference_sql"]).rows
    cache_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    return rows


def run(mode: str, refresh_cache: bool) -> int:
    cases = json.loads(Path("evaluation/golden_cases.json").read_text(encoding="utf-8"))
    llm = None
    if mode == "mock":
        os.environ["USE_STUB_LLM"] = "true"
        os.environ["USE_MOCK_BQ"] = "true"
        _disable_network_for_mock()
        runner = MockBigQueryRunner()
        llm = DeterministicStubLLM()
    else:
        os.environ["USE_STUB_LLM"] = os.getenv("USE_STUB_LLM", "false")
        os.environ["USE_MOCK_BQ"] = "false"
        runner = make_runner()

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SQLITE_PATH"] = str(Path(tmp) / "reports.sqlite3")
        os.environ["LOG_PATH"] = str(Path(tmp) / "agent.jsonl")
        store = ReportsStore(Path(tmp) / "reports.sqlite3")
        agent = RetailInsightsAgent(user_id="eval_manager", store=store, runner=runner, llm=llm)
        cache_dir = Path("evaluation/expected_results") / mode
        passed = 0
        failures = []
        for case in cases:
            result = agent.answer(case["question"], thread_id=f"eval-{mode}")
            text = result.get("text", "")
            ok = True
            reasons: list[str] = []
            if case.get("assert_no_pii") and has_pii(text):
                ok = False; reasons.append("PII found in output")
            sql = result.get("sql", "")
            tables = extract_tables(sql) if sql else set()
            if tables and not tables <= set(case.get("expected_tables", [])):
                ok = False; reasons.append(f"unexpected tables {sorted(tables)}")
            if result.get("healing_attempts", 0) > MAX_HEALING_ATTEMPTS:
                ok = False; reasons.append("healing attempts exceeded cost shield")
            try:
                reference_rows = _reference_rows(case, runner, cache_dir, refresh_cache=refresh_cache)
            except Exception as exc:
                ok = False; reasons.append(str(exc)); reference_rows = []
            strat = case["strategy"]
            if strat == "execution_non_empty":
                if not result.get("rows"):
                    ok = False; reasons.append("empty agent rows")
                elif not compare_result_sets(result.get("rows", []), reference_rows):
                    ok = False; reasons.append("agent result does not match reference execution signature")
            if strat == "contains_branch_disclosure" and "no physical branches" not in text:
                ok = False; reasons.append("missing branch disclosure")
            if strat == "masked_output" and ("[REDACTED]" not in text or "I cannot reveal" not in text):
                ok = False; reasons.append("missing redaction/refusal marker")
            if strat == "graceful_empty" and ("No rows" not in text or "2019-01-01" not in text):
                ok = False; reasons.append("missing graceful empty message with data range")
            if ok:
                passed += 1
            else:
                failures.append((case["id"], reasons))

        if hasattr(agent, "reports") and hasattr(agent.reports, "conn"):
            agent.reports.conn.close()
        if hasattr(agent, "_compiled_graph") and agent._compiled_graph is not None:
            compiled = getattr(agent._compiled_graph, "_compiled", None)
            if compiled is not None and hasattr(compiled, "checkpointer"):
                cp = compiled.checkpointer
                if hasattr(cp, "conn") and cp.conn is not None:
                    try:
                        cp.conn.close()
                    except Exception:
                        pass
    rate = passed / len(cases)
    print(f"Mode: {mode}")
    print(f"Pass rate: {passed}/{len(cases)} = {rate:.0%}")
    if failures:
        print("Failures:", failures)
    return 0 if rate >= 0.8 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh expected result cache by executing reference_sql.")
    args = parser.parse_args()
    return run(args.mode, args.refresh_cache)


if __name__ == "__main__":
    raise SystemExit(main())
