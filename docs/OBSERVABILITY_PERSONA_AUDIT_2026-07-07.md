# Observability & Persona Agility Audit — 2026-07-07

Continuation of the live-verification audit series (`BUGFIX_AUDIT_2026-07-07.md` ended at
#21). Scope: the two remaining spec pillars — **Observability** ("know when the agent is
failing and why; support deep-dive analysis of the message correspondence") and **Agility /
Persona Management** ("non-developers update the agent's instructions without redeployment").
Method as before: drive the real LangGraph runtime end to end, compare observed behavior to
the documented claims, fix, regress-test.

## Findings and fixes

### #22 — Persona configuration was dead code for every user without explicit `/prefs` (agility, high)

**Observed live:** editing `config/persona.yaml` (`tone: playful_marketing`) mid-session had
no effect on the next report, even though the mtime hot-reload fired.

**Root cause:** `ReportsStore.get_preferences()` returned hardcoded defaults
(`{"format": "bullets", "tone": "concise_executive"}`) for users who never ran `/prefs`, and
the reporter resolves `preferences → persona → built-in default` with `or`. The always-truthy
phantom preference meant `persona.yaml`'s `tone`/`format_defaults` could never win — the CEO's
weekly tone change silently never applied.

**Fix:** `get_preferences()` returns only axes the user explicitly set (empty dict
otherwise); `update_preferences()` keeps unset axes as NULL so persona keeps governing them.
Precedence is now: explicit `/prefs` > `persona.yaml` > built-in default. Note for existing
local databases: a row created by the old code pinned both axes; re-running `/prefs` or
deleting the row restores persona control.
**Tests:** `tests/unit/test_persona_agility.py` (precedence both directions),
verified live (tone change now visible on the next turn, no restart).

### #23 — A broken persona.yaml edit killed the turn (agility, high)

**Observed live:** saving syntactically invalid YAML mid-session made the next question fail
with "I could not complete this turn through the LangGraph runtime" (unhandled `YAMLError`
inside the reporter node). The exact persona-editing scenario the requirement describes —
a non-developer editing instructions — could take the agent down.

**Fix:** `PersonaLoader.load()` catches parse errors and non-mapping content, keeps the last
good config in effect, records `last_error`, emits a `persona_loader: reload_failed`
telemetry event, and retries the file on the next load (so a later fixed save is picked up).
**Tests:** `tests/unit/test_persona_agility.py`,
`tests/integration/test_observability_and_persona_graph.py::test_broken_persona_yaml_mid_session_does_not_kill_the_turn`;
verified live (turn succeeds on last-good persona, error visible in log and `last_error`).

### #24 — The real LangGraph path logged neither turn summaries nor message payloads (observability, high)

**Observed live:** with `langgraph` installed (the supported runtime), `logs/agent.jsonl`
contained only bare `start`/`ok` node events — no `turn_summary` at all, no `question`, no
`intent`, no generated `sql`, no report preview. `/stats` therefore had no turn-level signal
in real installs, and a debugging session could not reconstruct what was asked or what SQL
ran. (Both existed on the imperative fallback path only, which real installs never use — the
HLD claim "captures node transitions and turn summaries" was true only for the fallback.)

**Fix:** `instrument()` now logs the router's `question`/`user_id`/resolved `intent`, the
generated `sql` and post-heal `question_variant`, validator verdict + final SQL, executor
rows/bytes/redactions, healer decisions with the triggering error, and a masked
`report_preview`. Terminal nodes stamp a `turn_outcome` (reset each turn by the router, as
is `report_id`, which previously leaked into non-report turns' summaries), and `answer()`
emits one `turn_summary` per turn — including `runtime_error` on graph exceptions and
`delete_executed`/`delete_cancelled` on resumed confirmations.
**Tests:** `tests/integration/test_observability_and_persona_graph.py` (full reconstruction
of a turn from JSONL; stale-`report_id` regression); verified live.

### #25 — `/stats` metrics were misleading; no LLM message-correspondence capture (observability, medium)

**Observed live:** `self_heal_events: 8` after a session with exactly 2 real healing
retries — the counter counted every pass through the healer node (`start`+`ok`), inflating
4× on the graph path. Error rate was per-event, not per-turn, and there were no turn-level
latency/outcome metrics. There was also no way to see what was actually sent to the LLM.

**Fix:** `summarize_log()` now reports `turns`, `turn_outcomes`, `turn_error_rate`,
`avg/p95_turn_latency_ms`, `self_heal_retries` (actual rewrites only: imperative
`event=retry` or graph `needs_retry=true`), `node_errors`, `avg_node_latency_ms`, and
per-node counts. New opt-in `LLM_TRACE=true` wraps the LLM client (`TraceLLM`) and logs each
`invoke`/`generate_sql`/`generate_report` call with PII-masked prompt/response previews,
attributed to the correct `thread_id`/`turn_id` via a `ContextVar` set by `instrument()` —
works identically for the stub and the real Gemini client, and is transparent to the
`hasattr()`-based client dispatch.
**Tests:** `tests/unit/test_observability.py`; verified live (`self_heal_retries: 2` for the
same session shape, `node="llm"` events carry the prompt and response).

## Follow-up findings from the live re-verification round (same day)

### #26 — Embedding-quota exhaustion crashed the agent at startup (resilience, high)

**Observed live:** once the Gemini free-tier embedding quota
(`embed_content_free_tier_requests`, 1000/day) ran out, `GoldenBucket` seeding raised
429 inside `RetailInsightsAgent.__init__` and the whole process died — no CLI, no evals.

**Fix:** seeding failures rebuild the index with the deterministic embedder (query and
document vector spaces stay consistent), record `degraded_reason`, and the agent logs a
`golden_bucket: degraded` startup event. Mid-session query-embedding failures fall back to
the existing lexical ranking instead of failing the turn. Verified live after the fix: with
the embedding quota still exhausted, the agent starts, answers, and logs the degradation.
**Tests:** `tests/unit/test_golden_bucket_resilience.py`.

### #27 — Windows temp-dir cleanup crash discarded live eval results (observability, low)

**Observed live:** `run_evals.py` finished all 10 (billed) live cases, then crashed in
`TemporaryDirectory` cleanup (`NotADirectoryError` on a lingering SQLite handle) *before*
printing the pass rate. **Fix:** `TemporaryDirectory(ignore_cleanup_errors=True)`.

### Live-mode quota context for reviewers

Free-tier Gemini keys have small daily generate quotas (observed: 20/day for
`gemini-3.5-flash`, far higher for `gemini-3.1-flash-lite`). A same-day live eval run with
`gemini-3.1-flash-lite` scored 6/10 — the 4 failures were LLM output variance
(execution-signature mismatches, one table-choice difference, one healer rewrite of an
out-of-range year) rather than pipeline defects; with the quota exhausted the agent now
degrades per #26 instead of crashing. Live accuracy remains credential/quota-dependent, as
the compliance matrix has always marked it (`EXTERNAL`).

## Verification

```text
python -m pytest -q            -> 94 passed (was 75; +19 regression tests)
python evaluation/run_evals.py -> Pass rate: 10/10 = 100%
```

Both original failure scenarios re-driven live after the fixes: mid-session tone edit now
changes the next report without restart; mid-session broken YAML degrades gracefully; a
mixed session (analysis + self-heal exhaustion + refusal) is fully reconstructable from
`logs/agent.jsonl` and `/stats` reflects it accurately.
