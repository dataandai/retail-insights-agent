from __future__ import annotations

import os
from typing import Any

MAX_HEALING_ATTEMPTS = int(os.getenv("MAX_HEALING_ATTEMPTS", "2"))


def maybe_heal(question: str, sql: str, error: str, empty: bool, retries: int, llm, *, data_bounds: dict[str, Any] | None = None) -> dict:
    """Return a bounded retry decision for SQL errors or empty results.

    Both failure modes share the same hard retry budget to avoid runaway LLM calls and
    runaway BigQuery dry-runs. The caller decides how to feed the amended question back
    into SQL generation.
    """
    if retries >= MAX_HEALING_ATTEMPTS:
        message = "Retry budget exhausted; failing gracefully."
        if error:
            message = f"Retry budget exhausted after {MAX_HEALING_ATTEMPTS} attempt(s). Last error: {error}"
        if empty and data_bounds:
            message = (
                f"No rows found after {MAX_HEALING_ATTEMPTS} correction attempt(s). "
                f"The available {data_bounds.get('table', 'table')} data covers "
                f"{data_bounds.get('min_value') or 'unknown'} to {data_bounds.get('max_value') or 'unknown'}."
            )
        return {"should_retry": False, "message": message, "retries": retries}
    if error:
        healed_question = f"{question}\nPrevious SQL failed with: {error}. Generate one corrected BigQuery SELECT using only the verified schema."
        return {"should_retry": True, "question": healed_question, "retries": retries + 1, "reason": "execution_error"}
    if empty:
        bounds_hint = ""
        if data_bounds:
            bounds_hint = f" Data range hint: {data_bounds.get('table')}.{data_bounds.get('column')} covers {data_bounds.get('min_value')} to {data_bounds.get('max_value')}."
        healed_question = f"{question}\nPrevious query returned zero rows.{bounds_hint} Relax overly strict filters if appropriate; otherwise explain the available data range."
        return {"should_retry": True, "question": healed_question, "retries": retries + 1, "reason": "empty_result"}
    return {"should_retry": False, "retries": retries}
