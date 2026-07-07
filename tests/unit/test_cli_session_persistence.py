from src.database.reports_store import ReportsStore


def test_cli_pending_confirmation_persists_in_sqlite(tmp_path):
    db = tmp_path / "reports.sqlite3"
    store1 = ReportsStore(db)
    payload = {"ids": [1], "token": "CONFIRM DELETE", "owner_id": "manager"}
    store1.save_cli_session_state(thread_id="retail-insights:manager", owner_id="manager", pending_confirmation=payload, last_turn={"turn_id": "t1"})

    store2 = ReportsStore(db)
    restored = store2.load_cli_session_state(thread_id="retail-insights:manager", owner_id="manager")
    assert restored["pending_confirmation"] == payload
    assert restored["last_turn"]["turn_id"] == "t1"


def test_clear_pending_confirmation_keeps_last_turn(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    store.save_cli_session_state(thread_id="t", owner_id="u", pending_confirmation={"ids": [1]}, last_turn={"turn_id": "last"})
    store.clear_pending_confirmation(thread_id="t", owner_id="u")
    restored = store.load_cli_session_state(thread_id="t", owner_id="u")
    assert restored["pending_confirmation"] is None
    assert restored["last_turn"]["turn_id"] == "last"
