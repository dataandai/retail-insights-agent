import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.agent.graph import RetailInsightsAgent
from src.agent.nodes.self_healer import MAX_HEALING_ATTEMPTS
from src.database.bigquery_runner import MockBigQueryRunner, QueryResult
from src.database.reports_store import ReportsStore


def test_top_customers_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="smoke")
    result = agent.answer("Who are our top 10 customers by total spend?", thread_id="t")
    assert "example.com" not in result["text"]
    assert "email" not in result.get("sql", "").lower()
    assert result["rows"]


def test_branch_disclosure(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="smoke")
    result = agent.answer("Why is the Texas branch underperforming compared to California?", thread_id="t")
    assert "no physical branches" in result["text"]


def test_empty_result_self_heals_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="smoke")
    result = agent.answer("Show monthly revenue in 2099 future range", thread_id="t")
    assert result["healing_attempts"] == MAX_HEALING_ATTEMPTS
    assert "2019-01-01" in result["text"]


def test_build_langgraph_runtime_analysis_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    from src.agent.graph import build_langgraph
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="graph")
    graph = build_langgraph(agent)
    out = graph.invoke({"thread_id": "g", "turn_id": "1", "user_id": "graph", "question": "Who are our top 10 customers by total spend?", "original_question": "Who are our top 10 customers by total spend?", "retries": 0})
    assert out.get("text") or out.get("report")
    assert "example.com" not in (out.get("text") or out.get("report", ""))


class _FlakySqlLLM:
    """First generate_sql call fails at execution (mock triggers on 'unknown_column');
    the second, healed call succeeds - proving self-heal can recover mid-budget, not just
    exhaust it."""

    def __init__(self):
        self.sql_calls = 0

    def generate_sql(self, question, few_shots=""):
        self.sql_calls += 1
        if self.sql_calls == 1:
            return "SELECT unknown_column FROM order_items"
        return "SELECT id FROM order_items LIMIT 1"

    def generate_report(self, question, rows, preference_format="bullets", persona=None, tone="concise_executive", empty_explanation="", few_shots=""):
        return f"Business takeaway: {len(rows)} row(s) returned after self-correction."


def test_self_healer_recovers_mid_budget_not_just_exhausts(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    llm = _FlakySqlLLM()
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), llm=llm, user_id="smoke")
    result = agent.answer("Show me something that will need one correction", thread_id="heal-success")
    assert llm.sql_calls == 2
    assert "self-correction" in result["text"]
    assert result.get("healing_attempts") == 1


def test_unsupported_email_action_returns_router_specific_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="smoke")
    result = agent.answer("Email the weekly report to my team", thread_id="t")
    assert "does not send or email reports" in result["text"]


class _AlwaysFailingRunner(MockBigQueryRunner):
    def execute(self, sql: str) -> QueryResult:
        return QueryResult(rows=[], sql=sql, error="Unrecognized name: doomed_column")


def test_execution_error_surfaces_as_graceful_failure_without_saved_report(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    store = ReportsStore(tmp_path / "reports.sqlite3")
    agent = RetailInsightsAgent(runner=_AlwaysFailingRunner(), store=store, user_id="smoke")
    result = agent.answer("Top brands by revenue", thread_id="t")
    assert "could not run a safe query" in result["text"]
    assert "Unrecognized name: doomed_column" in result["text"]
    assert not store.list_reports("smoke", keyword="brands")


def test_branch_disclosure_does_not_leak_across_turns_in_same_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    agent = RetailInsightsAgent(store=ReportsStore(tmp_path / "reports.sqlite3"), user_id="smoke")
    thread_id = "persistent-thread"
    first = agent.answer("Why is the Texas branch underperforming compared to California?", thread_id=thread_id)
    assert "no physical branches" in first["text"]
    second = agent.answer("What is revenue by product category?", thread_id=thread_id)
    assert "no physical branches" not in second["text"]


def test_graph_delete_confirmation_fallback_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    store = ReportsStore(tmp_path / "reports.sqlite3")
    rid = store.save_report(owner_id="graph", question="Acme Corp sales", sql="SELECT 1 FROM orders", report_text="x", tags="orders")
    agent = RetailInsightsAgent(store=store, user_id="graph")
    pending_result = agent.answer("Delete all reports mentioning Acme Corp", thread_id="gdel")
    assert pending_result.get("pending_confirmation")
    result = agent.resume_delete(pending_result["pending_confirmation"], "CONFIRM DELETE", thread_id="gdel")
    assert result["deleted"] == 1
    assert not store.list_reports("graph", keyword="Acme Corp")


def test_cross_user_cannot_resume_another_users_pending_delete(tmp_path, monkeypatch):
    """thread_id is caller-suppliable and predictable (retail-insights:<user_id>), and the
    subset/all-reports confirmation tokens are fixed, non-secret strings. A different
    authenticated user resuming someone else's thread with the right-looking token must not
    be able to execute their delete."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    store = ReportsStore(tmp_path / "reports.sqlite3")
    rid = store.save_report(owner_id="manager_a", question="Client X sales", sql="SELECT 1", report_text="x", tags="clientx")
    thread_a = "retail-insights:manager_a"
    agent_a = RetailInsightsAgent(store=store, user_id="manager_a")
    pending = agent_a.answer("Delete all reports mentioning Client X", thread_id=thread_a)
    assert pending.get("pending_confirmation")

    agent_b = RetailInsightsAgent(store=store, user_id="manager_b")
    hijack_attempt = agent_b.resume_delete(None, "CONFIRM DELETE", thread_id=thread_a)
    assert hijack_attempt["deleted"] == 0
    assert hijack_attempt["cancelled"]
    assert store.conn.execute("SELECT deleted_at FROM reports WHERE id=?", (rid,)).fetchone()["deleted_at"] is None
    # Note: LangGraph's interrupt() is single-use, so this blocked hijack attempt also
    # consumes the pending confirmation - the legitimate owner would need to re-issue the
    # delete request for a fresh prompt. That's a secondary UX rough edge, not a security gap:
    # nothing was deleted, which is the property this test guards.
