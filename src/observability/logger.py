"""Structured JSONL telemetry for local-first debugging."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import Any

# Set by the graph's instrument() wrapper at every node start so components that
# have no direct access to graph state (e.g. the LLM trace wrapper) can still
# attribute their events to the correct thread/turn.
current_turn: ContextVar[tuple[str, str]] = ContextVar("current_turn", default=("unknown", "unknown"))


def _default_log_path() -> Path:
    return Path(os.getenv("LOG_PATH", "logs/agent.jsonl"))


class JsonlLogger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_log_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, *, thread_id: str, turn_id: str, node: str, event: str, **fields: Any) -> None:
        row = {
            "ts": time.time(),
            "thread_id": thread_id,
            "turn_id": turn_id,
            "node": node,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def summarize_log(path: str | Path | None = None) -> dict[str, Any]:
    """Aggregate agent-level metrics from the JSONL event log.

    Turn-level metrics come from `turn_summary` events (one per completed turn);
    node-level metrics come from the per-node start/ok/error events. A self-heal
    retry is a `self_healer` event that actually rewrote the question — either the
    imperative path's explicit event=="retry" or the graph path's needs_retry=True —
    not any pass through the healer node.
    """
    p = Path(path) if path else _default_log_path()
    empty = {
        "events": 0,
        "turns": 0,
        "turn_outcomes": {},
        "turn_error_rate": 0.0,
        "avg_turn_latency_ms": 0.0,
        "p95_turn_latency_ms": 0.0,
        "self_heal_retries": 0,
        "node_errors": 0,
        "avg_node_latency_ms": 0.0,
        "nodes": {},
    }
    if not p.exists():
        return empty
    events: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not events:
        return empty

    turn_summaries = [e for e in events if e.get("node") == "turn_summary"]
    turn_outcomes = Counter(e.get("event") for e in turn_summaries)
    turn_latencies = [float(e["latency_ms"]) for e in turn_summaries if "latency_ms" in e]
    failed_turns = sum(count for outcome, count in turn_outcomes.items() if outcome in ("graceful_failure", "runtime_error", "error"))

    node_events = [e for e in events if e.get("node") != "turn_summary"]
    node_latencies = [float(e["latency_ms"]) for e in node_events if "latency_ms" in e]
    node_errors = sum(1 for e in node_events if e.get("event") == "error")
    self_heal_retries = sum(
        1
        for e in events
        if e.get("node") == "self_healer" and (e.get("event") == "retry" or e.get("needs_retry") is True)
    )

    return {
        "events": len(events),
        "turns": len(turn_summaries),
        "turn_outcomes": dict(turn_outcomes),
        "turn_error_rate": round(failed_turns / max(1, len(turn_summaries)), 4),
        "avg_turn_latency_ms": round(sum(turn_latencies) / max(1, len(turn_latencies)), 2),
        "p95_turn_latency_ms": round(_percentile(turn_latencies, 95), 2),
        "self_heal_retries": self_heal_retries,
        "node_errors": node_errors,
        "avg_node_latency_ms": round(sum(node_latencies) / max(1, len(node_latencies)), 2),
        "nodes": dict(Counter(e.get("node") for e in node_events)),
    }
