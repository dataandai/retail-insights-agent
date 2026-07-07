"""The CLI must survive an unexpected exception mid-turn and keep answering afterward."""
import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

import src.cli as cli_module
from src.agent.graph import RetailInsightsAgent


def test_cli_survives_unexpected_exception_and_keeps_answering(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    monkeypatch.setenv("RETAIL_INSIGHTS_AUTH_REQUIRED", "false")

    original_answer = RetailInsightsAgent.answer

    def flaky_answer(self, question, **kwargs):
        if question == "TRIGGER_CRASH":
            raise RuntimeError("simulated unexpected failure")
        return original_answer(self, question, **kwargs)

    monkeypatch.setattr(RetailInsightsAgent, "answer", flaky_answer)

    inputs = iter(["TRIGGER_CRASH", "Who are our top 10 customers by total spend?", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    rc = cli_module.main([])

    out = capsys.readouterr().out
    assert "recoverable error" in out, "the CLI must report the failure without dying"
    assert "total_spend" in out, "the very next turn must still be answered normally"
    assert rc == 0
