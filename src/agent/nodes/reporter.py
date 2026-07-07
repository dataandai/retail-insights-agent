from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from src.security.pii_patterns import mask_text


class PersonaLoader:
    def __init__(self, path: str | Path = "config/persona.yaml", on_event=None):
        self.path = Path(path)
        self._mtime = 0.0
        self._cache: dict[str, Any] = {}
        self.last_error: str = ""
        # Optional callback(event=..., **fields) so the agent can route reload
        # outcomes into its telemetry without coupling this module to the logger.
        self._on_event = on_event

    def load(self) -> dict[str, Any]:
        """Return the current persona config, hot-reloading on mtime change.

        persona.yaml is edited by non-developers mid-session, so a bad edit must
        never take a turn down: on parse failure (or non-mapping content) the last
        good config stays in effect, the error is surfaced via on_event/last_error,
        and the next load() retries the file.
        """
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return {}
        if mtime != self._mtime:
            try:
                loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(f"persona.yaml must be a YAML mapping, got {type(loaded).__name__}")
                self._cache = loaded
                self._mtime = mtime
                self.last_error = ""
                if self._on_event:
                    self._on_event(event="reloaded", keys=sorted(self._cache.keys()))
            except Exception as exc:
                self.last_error = str(exc)
                if self._on_event:
                    self._on_event(event="reload_failed", error=str(exc))
        return self._cache


def generate_report(
    question: str,
    rows: list[dict],
    llm,
    preferences: dict[str, str],
    persona: dict,
    *,
    empty_explanation: str = "",
    few_shots: str = "",
) -> str:
    fmt = preferences.get("format") or persona.get("format_defaults", {}).get("preferred_format", "bullets")
    tone = preferences.get("tone") or persona.get("tone", "concise_executive")
    if not rows and empty_explanation:
        return mask_text(empty_explanation)
    if hasattr(llm, "generate_report"):
        return mask_text(llm.generate_report(question, rows, preference_format=fmt, persona=persona, tone=tone, empty_explanation=empty_explanation, few_shots=few_shots))
    prompt = f"""Persona config: {persona}
User preference format: {fmt}
User preference tone: {tone}
Prior human-analyst trios for similar questions. Mimic how these analysts framed
insights and structured their takeaways, but describe only the rows given below —
never invent numbers from the examples:
{few_shots}
Question: {question}
Rows with PII already redacted before this prompt: {rows}
Write an executive retail analytics report. Never include raw PII; if a redacted value is present, keep it as [REDACTED]."""
    content = llm.invoke(prompt).content
    if isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and "text" in block:
                text += block["text"]
            elif isinstance(block, str):
                text += block
            else:
                text += str(block)
        content = text
    return mask_text(content)
