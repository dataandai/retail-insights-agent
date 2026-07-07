"""Agent-level metrics aggregation and the opt-in LLM prompt/response trace."""
import json
import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.llm.client import DeterministicStubLLM
from src.observability.llm_trace import TraceLLM, maybe_trace_llm
from src.observability.logger import JsonlLogger, current_turn, summarize_log


def _write_events(path, events):
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_summarize_log_turn_level_metrics(tmp_path):
    log = tmp_path / "agent.jsonl"
    _write_events(log, [
        {"node": "turn_summary", "event": "ok", "latency_ms": 100.0},
        {"node": "turn_summary", "event": "graceful_failure", "latency_ms": 300.0},
        {"node": "turn_summary", "event": "refused", "latency_ms": 10.0},
        {"node": "executor", "event": "error", "latency_ms": 5.0},
        # Imperative path logs an explicit retry event.
        {"node": "self_healer", "event": "retry", "retries": 1},
        # Graph path logs ok with needs_retry flag - only True counts as a retry.
        {"node": "self_healer", "event": "ok", "needs_retry": True},
        {"node": "self_healer", "event": "ok", "needs_retry": False},
        {"node": "self_healer", "event": "start"},
    ])
    stats = summarize_log(log)
    assert stats["turns"] == 3
    assert stats["turn_outcomes"] == {"ok": 1, "graceful_failure": 1, "refused": 1}
    assert stats["turn_error_rate"] == round(1 / 3, 4)
    assert stats["avg_turn_latency_ms"] == round((100 + 300 + 10) / 3, 2)
    assert stats["p95_turn_latency_ms"] == 300.0
    assert stats["self_heal_retries"] == 2  # not 4: node passes are not retries
    assert stats["node_errors"] == 1


def test_summarize_log_empty_file_shape(tmp_path):
    stats = summarize_log(tmp_path / "missing.jsonl")
    assert stats["turns"] == 0
    assert stats["self_heal_retries"] == 0


def test_trace_llm_logs_prompt_and_response_with_turn_context(tmp_path):
    log_path = tmp_path / "agent.jsonl"
    llm = TraceLLM(DeterministicStubLLM(), JsonlLogger(log_path))
    current_turn.set(("thread-x", "turn-y"))
    sql = llm.generate_sql("Top brands by revenue")
    assert "brand" in sql
    rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    (event,) = [r for r in rows if r["node"] == "llm"]
    assert event["thread_id"] == "thread-x"
    assert event["turn_id"] == "turn-y"
    assert event["method"] == "generate_sql"
    assert "Top brands by revenue" in event["prompt_preview"]
    assert "brand" in event["response_preview"]


def test_trace_llm_is_transparent_for_hasattr_dispatch(tmp_path):
    class InvokeOnly:
        def invoke(self, prompt):
            return type("Msg", (), {"content": "hello"})()

    traced_stub = TraceLLM(DeterministicStubLLM(), JsonlLogger(tmp_path / "a.jsonl"))
    traced_real = TraceLLM(InvokeOnly(), JsonlLogger(tmp_path / "b.jsonl"))
    assert hasattr(traced_stub, "generate_report")
    assert not hasattr(traced_real, "generate_report")
    assert traced_real.invoke("hi").content == "hello"


def test_maybe_trace_llm_is_opt_in(tmp_path, monkeypatch):
    inner = DeterministicStubLLM()
    monkeypatch.delenv("LLM_TRACE", raising=False)
    assert maybe_trace_llm(inner, JsonlLogger(tmp_path / "a.jsonl")) is inner
    monkeypatch.setenv("LLM_TRACE", "true")
    assert isinstance(maybe_trace_llm(inner, JsonlLogger(tmp_path / "a.jsonl")), TraceLLM)
