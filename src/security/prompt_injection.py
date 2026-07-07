"""Deterministic prompt-injection and control-plane override detection."""
from __future__ import annotations

import re

# User input is data. These patterns identify attempts to change the agent's control
# plane, reveal hidden prompts, or execute destructive developer instructions.
INJECTION_PATTERNS = [
    re.compile(r"\bignore (all |the |any )?(previous|prior|above) instructions\b", re.I),
    re.compile(r"\b(system|developer) prompt\b", re.I),
    re.compile(r"\bshow (me )?(your|the) (hidden )?(instructions|prompt|system prompt)\b", re.I),
    re.compile(r"\breveal (your|the) (instructions|system prompt|developer message)\b", re.I),
    re.compile(r"\benter developer mode\b", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"\b(drop|truncate)\s+(the\s+)?(\w+\s+)?tables?\b", re.I),
    re.compile(r"\b(drop|truncate)\s+table\s+\w+", re.I),
    re.compile(r"\bdelete\s+from\s+\w+", re.I),
    re.compile(r"\btruncate\s+(users|orders|order_items|products|distribution_centers)\b", re.I),
    re.compile(r"\bexecute (ddl|dml|delete|update|insert|drop)\b", re.I),
]

REFUSAL_MESSAGE = (
    "I can't follow instructions that try to override the agent's operating rules, "
    "reveal hidden prompts or system prompt content, or perform unsafe database actions. Ask a normal retail "
    "analytics question and I can help with a safe, validated SELECT-only analysis."
)


def is_prompt_injection_attempt(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in INJECTION_PATTERNS)
