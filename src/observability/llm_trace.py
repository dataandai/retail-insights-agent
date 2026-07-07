"""Opt-in LLM prompt/response tracing for deep-dive debugging.

Enable with LLM_TRACE=true. Every LLM call (real `invoke` or the stub's
`generate_sql`/`generate_report`) is logged to the JSONL telemetry with the
thread/turn it belongs to, a masked prompt preview, and a masked response
preview — the "message correspondence" needed to debug a bad turn. Previews
are PII-masked and truncated; raw rows entering prompts are already redacted
upstream, this is defense in depth for the local log file.
"""
from __future__ import annotations

import os
import time
from typing import Any

from src.observability.logger import JsonlLogger, current_turn
from src.security.pii_patterns import mask_text

PREVIEW_CHARS = 1000
_TRACED_METHODS = {"invoke", "generate_sql", "generate_report"}


def _preview(value: Any) -> str:
    content = getattr(value, "content", value)
    return mask_text(str(content))[:PREVIEW_CHARS]


class TraceLLM:
    """Transparent proxy: delegates everything to the inner LLM, logging traced calls.

    hasattr() dispatch in sql_generator/reporter keeps working because attribute
    lookups fall through to the inner client (AttributeError propagates for
    methods the inner client does not define).
    """

    def __init__(self, inner: Any, log: JsonlLogger):
        self.inner = inner
        self.log = log

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if name not in _TRACED_METHODS or not callable(attr):
            return attr
        log = self.log

        def traced(*args: Any, **kwargs: Any) -> Any:
            thread_id, turn_id = current_turn.get()
            started = time.time()
            prompt_preview = _preview(args[0]) if args else _preview(kwargs)
            try:
                out = attr(*args, **kwargs)
            except Exception as exc:
                log.event(
                    thread_id=thread_id, turn_id=turn_id, node="llm", event="error",
                    method=name, latency_ms=round((time.time() - started) * 1000, 2),
                    prompt_preview=prompt_preview, error=str(exc),
                )
                raise
            log.event(
                thread_id=thread_id, turn_id=turn_id, node="llm", event="ok",
                method=name, latency_ms=round((time.time() - started) * 1000, 2),
                prompt_preview=prompt_preview, response_preview=_preview(out),
            )
            return out

        return traced


def maybe_trace_llm(llm: Any, log: JsonlLogger) -> Any:
    if os.getenv("LLM_TRACE", "false").lower() == "true":
        return TraceLLM(llm, log)
    return llm
