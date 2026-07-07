from src.database.reports_store import ReportsStore


def test_delete_scope_keyword_escapes_like_wildcards(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    # Report with literal percent in the question.
    target = store.save_report(owner_id="u1", question="Report mentioning 100% cotton", sql="SELECT 1 FROM orders", report_text="100% cotton", tags="products")
    # Report that would match if % were treated as a wildcard.
    store.save_report(owner_id="u1", question="Report mentioning 100X cotton", sql="SELECT 1 FROM orders", report_text="100X cotton", tags="products")
    matches = store.resolve_delete_scope(owner_id="u1", keyword="100% cotton")
    assert [m.id for m in matches] == [target]
