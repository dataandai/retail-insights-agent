import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.agent.graph import ConfiguredCompiledGraph, LocalCompiledGraph, RetailInsightsAgent, build_langgraph
from src.database.reports_store import ReportsStore


def test_build_langgraph_uses_real_runtime_when_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="graph")
    graph = build_langgraph(agent)
    assert not isinstance(graph, LocalCompiledGraph)
    assert isinstance(graph, ConfiguredCompiledGraph)
    assert getattr(graph, "checkpointer", None) is not None


def test_real_graph_logs_meaningful_node_transitions(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    log_path = tmp_path / "agent.jsonl"
    monkeypatch.setenv("LOG_PATH", str(log_path))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="graph")
    result = agent.answer("Who are our top 10 customers by total spend?", thread_id="telemetry")
    assert result["rows"]
    log_text = log_path.read_text(encoding="utf-8")
    for node in ["router", "retriever", "sql_generator", "sql_validator", "executor", "self_healer", "reporter"]:
        assert f'"node": "{node}"' in log_text
