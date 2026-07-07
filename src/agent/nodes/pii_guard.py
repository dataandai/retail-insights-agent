from __future__ import annotations

from src.security.pii_patterns import mask_records, mask_text


def guard_rows(rows: list[dict]) -> list[dict]:
    return mask_records(rows)


def guard_report(report: str) -> str:
    return mask_text(report)
