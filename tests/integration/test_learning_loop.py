"""System-level Learning Loop: feedback -> human-reviewed Golden Bucket promotion."""
import os
import sys

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.database.reports_store import ReportsStore
from src.knowledge import promote_trio
from src.knowledge.golden_bucket import GoldenBucket

QUESTION = "What is the average shipping delay by warehouse zone?"
SQL = "SELECT dc.name AS distribution_center, COUNT(*) AS orders FROM `bigquery-public-data.thelook_ecommerce.order_items` oi JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id JOIN `bigquery-public-data.thelook_ecommerce.distribution_centers` dc ON p.distribution_center_id = dc.id GROUP BY distribution_center"
REPORT = "Business takeaway: shipping delay is concentrated in two warehouse zones."


def _seed_feedback(tmp_path, monkeypatch) -> tuple[ReportsStore, int]:
    db_path = tmp_path / "reports.sqlite3"
    monkeypatch.setenv("SQLITE_PATH", str(db_path))
    store = ReportsStore(db_path)
    fid = store.add_feedback(turn_id="t1", user_id="manager_a", rating="good", note="promote this", question=QUESTION, sql=SQL, report_text=REPORT)
    return store, fid


def test_promotion_refused_without_reviewed_flag(tmp_path, monkeypatch, capsys):
    _, fid = _seed_feedback(tmp_path, monkeypatch)
    out_dir = tmp_path / "golden_bucket"
    monkeypatch.setattr(sys, "argv", ["promote_trio", str(fid), "--out", str(out_dir)])
    rc = promote_trio.main()
    assert rc != 0
    assert "--reviewed" in capsys.readouterr().out
    assert not (out_dir / f"gb_promoted_{fid:04d}.yaml").exists()


def test_promotion_succeeds_with_reviewed_and_is_retrievable_after_restart(tmp_path, monkeypatch):
    _, fid = _seed_feedback(tmp_path, monkeypatch)
    out_dir = tmp_path / "golden_bucket"
    monkeypatch.setattr(sys, "argv", ["promote_trio", str(fid), "--reviewed", "--out", str(out_dir)])
    rc = promote_trio.main()
    assert rc == 0
    promoted = out_dir / f"gb_promoted_{fid:04d}.yaml"
    assert promoted.exists()
    assert "human_reviewed" in promoted.read_text(encoding="utf-8")

    # A fresh GoldenBucket (simulating a new CLI process) must retrieve the promoted trio.
    fresh_bucket = GoldenBucket(out_dir)
    assert len(fresh_bucket.trios) == 1
    examples = fresh_bucket.search("What's the shipping delay trend across warehouse zones?", k=3)
    assert any(e.question == QUESTION for e in examples)


def test_already_constructed_bucket_does_not_live_reload(tmp_path, monkeypatch):
    """Unlike persona.yaml's mtime hot reload, GoldenBucket seeds its index once at
    construction. A trio promoted mid-session only becomes retrievable on the next
    process start, not inside an already-running agent's bucket instance."""
    _, fid = _seed_feedback(tmp_path, monkeypatch)
    out_dir = tmp_path / "golden_bucket"
    running_bucket = GoldenBucket(out_dir)
    assert len(running_bucket.trios) == 0

    monkeypatch.setattr(sys, "argv", ["promote_trio", str(fid), "--reviewed", "--out", str(out_dir)])
    assert promote_trio.main() == 0

    assert len(running_bucket.trios) == 0  # unchanged - no live reload
    assert len(GoldenBucket(out_dir).trios) == 1  # a fresh instance sees it
