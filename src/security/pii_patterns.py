"""Deterministic PII masking for tabular rows and generated prose.

The primary control is structural: runtime column denylisting derived from the live
BigQuery schema plus conservative built-in names. Regex masking is only a defense-in-depth
net for free text and final reports.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore

BASE_PII_COLUMN_RE = re.compile(r"(email|phone|street_address|postal_code)", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
# Phone matching requires phone-like formatting (a leading + or at least one -().
# separator) and an E.164-plausible digit count, so bare numeric analytics values
# (revenue sums, byte counts) in report prose are never redacted by mistake.
PHONE_RE = re.compile(
    r"(?<![\w-])"
    r"(?:"
    r"\+\d(?:[\s().-]?\d){7,14}"  # international: leading + and 8-15 digits
    r"|"
    r"\d(?:[\s]?\d)*(?:[().-][\s]?\d(?:[\s]?\d)*)+"  # national: at least one -(). separator
    r")"
    r"(?![\w-])"
)


def _plausible_phone(match: re.Match[str]) -> bool:
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    if not (8 <= digits <= 15):
        return False
    if text.startswith("+"):
        return True
    # A lone dot with no other separators is a decimal number, not a phone.
    separators = [ch for ch in text if ch in "().-"]
    return separators != ["."]
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
REDACTION = "[REDACTED]"

_RUNTIME_PII_COLUMNS: set[str] = set()


def configure_pii_columns(schema: Mapping[str, Iterable[str]] | None = None) -> set[str]:
    """Build the runtime PII column set from live schema introspection.

    The spec explicitly calls out that public theLook usually has no phone column, but the
    denylist must be adjusted from INFORMATION_SCHEMA at startup. This function keeps the
    broad phone regex safety net while only adding schema columns that actually exist.
    """
    global _RUNTIME_PII_COLUMNS
    found: set[str] = set()
    for columns in (schema or {}).values():
        for col in columns:
            name = str(col)
            if BASE_PII_COLUMN_RE.search(name):
                found.add(name.lower())
    _RUNTIME_PII_COLUMNS = found
    return set(_RUNTIME_PII_COLUMNS)


def runtime_pii_columns() -> set[str]:
    return set(_RUNTIME_PII_COLUMNS)


def is_pii_column(column_name: str) -> bool:
    name = (column_name or "").lower()
    return name in _RUNTIME_PII_COLUMNS or bool(BASE_PII_COLUMN_RE.search(name))


def contains_phone(text: str) -> bool:
    """True only for plausible phone matches — mirrors mask_text's redaction decision."""
    return any(_plausible_phone(m) for m in PHONE_RE.finditer(text))


def mask_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    protected: dict[str, str] = {}

    def keep_date(match: re.Match[str]) -> str:
        token = f"__DATE_TOKEN_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    masked = ISO_DATE_RE.sub(keep_date, value)
    masked = EMAIL_RE.sub(REDACTION, masked)
    masked = PHONE_RE.sub(lambda m: REDACTION if _plausible_phone(m) else m.group(0), masked)
    for token, original in protected.items():
        masked = masked.replace(token, original)
    return masked


def mask_record(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if is_pii_column(key):
            out[key] = REDACTION
        elif isinstance(value, str):
            out[key] = mask_text(value)
        else:
            out[key] = value
    return out


def mask_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [mask_record(r) for r in records]


def mask_dataframe(df):
    if pd is None:
        raise RuntimeError("pandas is required for mask_dataframe")
    masked = df.copy()
    for col in masked.columns:
        if is_pii_column(str(col)):
            masked[col] = REDACTION
        elif masked[col].dtype == "object":
            masked[col] = masked[col].map(mask_text)
    return masked


def count_redactions(before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]) -> int:
    total = 0
    for b, a in zip(before, after, strict=True):
        for key in a.keys():
            if b.get(key) != a.get(key):
                total += 1
    return total
