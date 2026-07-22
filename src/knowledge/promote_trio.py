"""Promote reviewed feedback into a Golden Bucket trio and rebuild its index.

Usage:
  python -m src.knowledge.promote_trio <feedback_id> --reviewed

The script refuses placeholder data unless --allow-draft is used, preventing silent Golden
Bucket poisoning while still giving operators a CLI-only promotion path.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from src.database.reports_store import ReportsStore
from src.knowledge.golden_bucket import GoldenBucket
from src.security.sql_guardrails import validate_sql


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("feedback_id", type=int)
    parser.add_argument("--out", default="data/golden_bucket")
    parser.add_argument("--reviewed", action="store_true", help="Human reviewer confirms this feedback is safe to promote.")
    parser.add_argument("--allow-draft", action="store_true", help="Write draft YAML even if fields are missing; not indexed.")
    args = parser.parse_args()

    store = ReportsStore()
    row = store.conn.execute("SELECT * FROM feedback WHERE id=?", (args.feedback_id,)).fetchone()
    if not row:
        print(f"No feedback found for id={args.feedback_id}")
        return 2

    question = (row["question"] or "").strip()
    sql = (row["sql"] or "").strip()
    report = (row["report_text"] or row["note"] or "").strip()
    missing = [name for name, value in {"question": question, "sql": sql, "report": report}.items() if not value]
    validation = validate_sql(sql) if sql else None
    if (missing or not validation or not validation.ok) and not args.allow_draft:
        print(f"Refusing promotion. Missing={missing}; SQL valid={bool(validation and validation.ok)}; reason={getattr(validation, 'reason', '')}")
        return 3
    if not args.reviewed and not args.allow_draft:
        print("Refusing promotion without --reviewed. A human must explicitly approve the trio.")
        return 4

    payload = {
        "question": question or "TODO: paste reviewed user question",
        "sql": validation.normalized_sql if validation and validation.ok else sql or "TODO: paste reviewed safe SQL",
        "report": report or "TODO: paste reviewed analyst report",
        "tags": ["promoted", "human_reviewed" if args.reviewed else "needs_review"],
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"gb_promoted_{args.feedback_id:04d}.yaml"
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    if args.reviewed and not args.allow_draft:
        bucket = GoldenBucket(out_dir)
        bucket.rebuild_index()
        print(f"Promoted {out_path} and rebuilt Golden Bucket index with {len(bucket.trios)} trio(s).")
    else:
        print(f"Wrote draft {out_path}. Review before committing; draft was not indexed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
