import time

from src.agent.nodes.confirmation import CONFIRM_ALL_TOKEN, CONFIRM_SUBSET_TOKEN, PendingConfirmation, confirm_delete, make_pending_confirmation, resolve_scope
from src.database.reports_store import ReportsStore


def test_owner_scoped_delete_confirmation(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    mine = store.save_report(owner_id="u1", question="Acme report", sql="SELECT 1", report_text="x")
    store.save_report(owner_id="u2", question="Acme report", sql="SELECT 1", report_text="x")
    scope = resolve_scope("Delete all reports mentioning Acme", "u1", store)
    assert [p.id for p in scope.previews] == [mine]
    cancelled = confirm_delete(scope, "yes", "u1", store)
    assert cancelled["cancelled"]
    ok = confirm_delete(scope, CONFIRM_SUBSET_TOKEN, "u1", store)
    assert ok["deleted"] == 1
    assert store.list_reports("u2")  # other user's report is untouched


def test_all_reports_requires_stronger_token(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    store.save_report(owner_id="u1", question="Q", sql="SELECT 1", report_text="x")
    scope = resolve_scope("Delete all reports ever", "u1", store)
    assert scope.token == CONFIRM_ALL_TOKEN
    cancelled = confirm_delete(scope, CONFIRM_SUBSET_TOKEN, "u1", store)
    assert cancelled["cancelled"]
    ok = confirm_delete(scope, CONFIRM_ALL_TOKEN, "u1", store)
    assert ok["deleted"] == 1


def test_pending_confirmation_expires(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    store.save_report(owner_id="u1", question="Acme", sql="SELECT 1", report_text="x")
    scope = resolve_scope("Delete all reports mentioning Acme", "u1", store)
    pending = make_pending_confirmation(scope, "u1", now=1000, ttl_seconds=1)
    result = confirm_delete(PendingConfirmation.from_dict(pending.to_dict()), CONFIRM_SUBSET_TOKEN, "u1", store, now=1002)
    assert result["cancelled"]
    assert store.list_reports("u1")


def test_delete_scope_latest_and_today(tmp_path):
    from src.database.reports_store import ReportsStore
    from src.agent.nodes.confirmation import resolve_scope
    import time
    store = ReportsStore(tmp_path / "reports.sqlite3")
    old = store.save_report(owner_id="u", question="old returns", sql="SELECT 1 FROM orders", report_text="old", tags="returns")
    time.sleep(0.01)
    latest = store.save_report(owner_id="u", question="latest returns", sql="SELECT 1 FROM orders", report_text="latest", tags="returns")
    scope = resolve_scope("Delete the latest report", "u", store)
    assert [p.id for p in scope.previews] == [latest]
    today = resolve_scope("Delete today's reports", "u", store)
    assert {p.id for p in today.previews} == {old, latest}
