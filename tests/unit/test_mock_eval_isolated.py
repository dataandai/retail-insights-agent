from evaluation.run_evals import run


def test_mock_eval_runs_with_network_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "reports.sqlite3"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "agent.jsonl"))
    assert run("mock", refresh_cache=False) == 0
