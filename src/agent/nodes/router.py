from __future__ import annotations

import re

from src.security.prompt_injection import is_prompt_injection_attempt


def route_intent(question: str) -> dict[str, str]:
    q = question.lower().strip()
    if is_prompt_injection_attempt(question):
        return {"intent": "refusal"}
    if re.search(r"\b(email|send|forward)\b.*\breports?\b", q) or re.search(r"\breports?\b.*\b(email|send|forward)\b", q):
        return {"intent": "unsupported_action", "unsupported_reason": "This CLI prototype can save and delete reports locally, but it does not send or email reports. Use /help for supported commands."}
    if re.search(r"\b(delete|remove|erase|purge)\b.*\breports?\b", q) or re.search(r"\breports?\b.*\b(delete|remove|erase|purge)\b", q):
        return {"intent": "delete_report"}
    if q.startswith("/schema") or "what columns" in q or "which columns" in q or re.search(r"\bschema\b", q):
        return {"intent": "schema"}
    if "distribution center" in q or "warehouse" in q or "fulfillment" in q:
        return {
            "intent": "analysis",
            "branch_disclosure": "This dataset is from an online retailer with no physical branches — for this answer, I'm reading 'branch' as the supply-side distribution center.",
            "branch_interpretation": "supply_side_distribution_center",
        }
    branch_like = (
        "branch" in q
        or "region" in q
        or re.search(r"\b(physical store|retail store|store performance|store revenue|underperforming store|store sales)\b", q)
    )
    if branch_like:
        return {
            "intent": "analysis",
            "branch_disclosure": "This dataset is from an online retailer with no physical branches — I'm reading 'branch' as the customers' state. Tell me if you meant distribution centers instead.",
            "branch_interpretation": "demand_side_state",
        }
    if "email" in q or "phone" in q or "contact info" in q or "raw row" in q:
        return {"intent": "pii_sensitive_analysis"}
    return {"intent": "analysis"}
