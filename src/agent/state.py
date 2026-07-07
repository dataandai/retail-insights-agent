from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    thread_id: str
    turn_id: str
    user_id: str
    question: str
    original_question: str
    intent: str
    branch_disclosure: str
    branch_interpretation: str
    refusal_reason: str
    few_shots: str
    sql: str
    validation_ok: bool
    validation_error: str
    tables: list[str]
    rows: list[dict[str, Any]]
    error: str
    empty: bool
    empty_explanation: str
    bytes_estimate: int
    retries: int
    needs_retry: bool
    report: str
    preferences: dict[str, str]
    pending_confirmation: dict[str, Any]
    delete_scope: Any
    confirmation_token: str

    answer_text: str
    graph_interrupt: dict[str, Any]
    delete_result: dict[str, Any]
    data_bounds: dict[str, Any]
    redactions_made: int

    text: str
    unsupported_reason: str
    turn_outcome: str
    report_id: int
    deleted: int
    cancelled: bool
    resuming_user_id: str
