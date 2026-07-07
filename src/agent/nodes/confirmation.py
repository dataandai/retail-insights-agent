from __future__ import annotations

import calendar
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.database.reports_store import ReportPreview, ReportsStore

CONFIRM_SUBSET_TOKEN = "CONFIRM DELETE"
CONFIRM_ALL_TOKEN = "CONFIRM DELETE ALL MY REPORTS"
DEFAULT_CONFIRMATION_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class DeleteScope:
    previews: list[ReportPreview]
    token: str
    blast_radius: str
    keyword: str | None = None
    tags: str | None = None
    all_reports: bool = False
    latest: bool = False
    start_ts: float | None = None
    end_ts: float | None = None


@dataclass(frozen=True)
class PendingConfirmation:
    token: str
    ids: list[int]
    owner_id: str
    created_at: float
    expires_at: float
    preview: list[dict[str, Any]]
    blast_radius: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingConfirmation":
        return cls(
            token=str(data["token"]),
            ids=[int(v) for v in data.get("ids", [])],
            owner_id=str(data["owner_id"]),
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
            preview=list(data.get("preview", [])),
            blast_radius=str(data.get("blast_radius", "filtered_subset")),
        )


def _day_bounds_utc(d: date) -> tuple[float, float]:
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)
    return calendar.timegm(start.timetuple()), calendar.timegm(end.timetuple())


def _week_bounds_utc(today: date) -> tuple[float, float]:
    start_date = today - timedelta(days=today.weekday())
    end_date = start_date + timedelta(days=7)
    return _day_bounds_utc(start_date)[0], _day_bounds_utc(end_date)[0]


def parse_delete_scope(question: str, *, now: float | None = None) -> dict[str, Any]:
    q = question.lower()
    now = now or time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date()

    m = re.search(r"mentioning\s+(.+)$", question, re.IGNORECASE)
    keyword = m.group(1).strip() if m else None
    if keyword:
        keyword = re.split(r"\bactually\b|\binstead\b", keyword, flags=re.IGNORECASE)[0].strip()
        keyword = keyword.strip("\"' .?")

    tag = None
    tag_match = re.search(r"(?:tagged|tag)\s+([\w-]+)", q)
    if tag_match:
        tag = tag_match.group(1).strip()

    latest = bool(re.search(r"\b(latest|most recent|last saved)\b", q))
    start_ts = end_ts = None
    if "today" in q or "today's" in q:
        start_ts, end_ts = _day_bounds_utc(today)
    elif "yesterday" in q:
        start_ts, end_ts = _day_bounds_utc(today - timedelta(days=1))
    elif "last week" in q:
        this_start, _ = _week_bounds_utc(today)
        last_start = datetime.fromtimestamp(this_start, timezone.utc).date() - timedelta(days=7)
        start_ts, end_ts = _day_bounds_utc(last_start)[0], this_start
    elif "this week" in q:
        start_ts, end_ts = _week_bounds_utc(today)

    all_reports = bool(re.search(r"\ball\b.*\breports\b|\ball saved reports\b|\breports ever\b", q)) and not any([keyword, tag, start_ts, end_ts, latest])
    return {"keyword": keyword or None, "tags": tag, "all_reports": all_reports, "latest": latest, "start_ts": start_ts, "end_ts": end_ts}


def parse_delete_keyword(question: str) -> tuple[str | None, bool]:
    parsed = parse_delete_scope(question)
    return parsed["keyword"], parsed["all_reports"]


def resolve_scope(question: str, owner_id: str, store: ReportsStore, *, now: float | None = None) -> DeleteScope:
    """Pure owner-scoped read. Safe to re-run before interrupt resume."""
    parsed = parse_delete_scope(question, now=now)
    previews = store.resolve_delete_scope(owner_id=owner_id, **parsed)
    token = CONFIRM_ALL_TOKEN if parsed["all_reports"] else CONFIRM_SUBSET_TOKEN
    blast = "all_reports" if parsed["all_reports"] else "filtered_subset"
    return DeleteScope(previews=previews, token=token, blast_radius=blast, **parsed)


def make_pending_confirmation(scope: DeleteScope, owner_id: str, *, now: float | None = None, ttl_seconds: int = DEFAULT_CONFIRMATION_TTL_SECONDS) -> PendingConfirmation:
    now = now or time.time()
    preview = [{"id": p.id, "question": p.question, "created_at": p.created_at, "tags": p.tags} for p in scope.previews[:5]]
    return PendingConfirmation(
        token=scope.token,
        ids=[p.id for p in scope.previews],
        owner_id=owner_id,
        created_at=now,
        expires_at=now + ttl_seconds,
        preview=preview,
        blast_radius=scope.blast_radius,
    )


def confirmation_prompt(scope: DeleteScope, owner_id: str) -> dict[str, Any]:
    pending = make_pending_confirmation(scope, owner_id)
    return {
        "action": "delete_reports",
        "owner_id": owner_id,
        "count": len(scope.previews),
        "preview": pending.preview,
        "token": pending.token,
        "blast_radius": scope.blast_radius,
        "expires_at": pending.expires_at,
        "scope": {
            "keyword": scope.keyword,
            "tags": scope.tags,
            "all_reports": scope.all_reports,
            "latest": scope.latest,
            "start_ts": scope.start_ts,
            "end_ts": scope.end_ts,
        },
    }


def confirm_delete(pending_or_scope: PendingConfirmation | DeleteScope, user_input: str, owner_id: str, store: ReportsStore, *, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    if isinstance(pending_or_scope, DeleteScope):
        pending = make_pending_confirmation(pending_or_scope, owner_id, now=now)
    else:
        pending = pending_or_scope
    if pending.owner_id != owner_id:
        return {"deleted": 0, "cancelled": True, "message": "Cancelled. Confirmation owner did not match the current user."}
    if now > pending.expires_at:
        return {"deleted": 0, "cancelled": True, "message": "Cancelled. The pending delete confirmation expired. Nothing was deleted."}
    if user_input.strip() != pending.token:
        return {"deleted": 0, "cancelled": True, "message": f"Cancelled. Nothing was deleted. Exact token required: {pending.token}"}
    deleted = store.soft_delete(owner_id=owner_id, report_ids=pending.ids, reason=f"exact-token confirmation: {pending.blast_radius}")
    return {"deleted": deleted, "cancelled": False, "message": f"Soft-deleted {deleted} owner-scoped report(s)."}
