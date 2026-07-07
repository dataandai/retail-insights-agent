# Bugfix Audit — 2026-07-07

A full-codebase bug hunt (two parallel explorations plus manual verification) found 15
confirmed defects, four of them severe. All are fixed in code with regression tests; none
required a scope or architecture change beyond one new graph node.

## Severe

1. **Wrong "delete all" confirmation token on CLI restart resume** — `src/cli.py` checked
   for the literal `CONFIRM DELETE ALL REPORTS` (missing "MY"), while the canonical token is
   `CONFIRM_ALL_TOKEN = "CONFIRM DELETE ALL MY REPORTS"` in `src/agent/nodes/confirmation.py`.
   A user typing the real token after a process restart would have it treated as a new
   analytics question instead of resuming the delete. Fixed by importing the shared
   constants instead of hardcoding the string.
2. **Undeclared LangGraph state channels** — `text`, `unsupported_reason`, `report_id`,
   `deleted`, `cancelled` were written by nodes but not declared in `AgentState`, so
   LangGraph silently dropped them between nodes (the router's tailored "does not send or
   email reports" reason never reached the user on the real graph path). Fixed by adding the
   five keys to `src/agent/state.py`.
3. **`PHONE_RE` redacted any 9+ digit number** in `src/security/pii_patterns.py`, including
   revenue sums and byte counts in report prose (`mask_text` runs on the full report). Fixed
   by requiring phone-like formatting (leading `+` or a `-().` separator) plus an
   E.164-plausible digit count (8-15); added `contains_phone()` for callers that only need a
   boolean (used by `evaluation/run_evals.py`'s `has_pii`, which had the same false-positive
   bug and dropped the mock eval pass rate to 10%).
4. **Deterministic embedding made "semantic" retrieval always return nothing** —
   `src/knowledge/golden_bucket.py`'s `DeterministicEmbedding.embed` hashed `task_type` into
   every token, so query (`RETRIEVAL_QUERY`) and document (`RETRIEVAL_DOCUMENT`) vectors for
   identical text had zero dot product. The lexical fallback masked this silently. Fixed by
   removing `task_type` from the hash input.

## Medium

5. **Graph path swallowed the real error on retry-budget exhaustion** — once
   `MAX_HEALING_ATTEMPTS` was hit for a validation/execution error, `route_after_healer` still
   sent the turn to `reporter_node`, which produced a report from empty rows with a generic
   "Retry budget exhausted; failing gracefully." message and *saved* the failed turn. The
   imperative path correctly returned the real error without saving. Fixed by adding a
   `graceful_failure` node/edge that only failed error cases route to; empty-but-successful
   results still go to `reporter`.
6. **Self-healer dropped the actual error message on exhaustion** for execution errors (only
   the empty-result branch got a useful message). Fixed in `src/agent/nodes/self_healer.py`
   to include `error` in the exhaustion message.
7. **Router's `"schema" in q` substring pre-empted delete/analysis intent** — e.g. "delete my
   reports about the schema migration" routed to schema instead of delete. Fixed by moving
   the schema check after delete/unsupported-action checks and requiring a word-boundary
   `\bschema\b` match.
8. **Plaintext `dev_token` backdoor with no gate, plus a dead `token_sha256` path** in
   `src/security/auth.py` — committed demo tokens worked unconditionally even with
   `RETAIL_INSIGHTS_AUTH_REQUIRED=true`, while configuring a user with only a hashed token
   made them unable to log in at all (the hash was read and discarded). Fixed: `token_sha256`
   is now checked with `hmac.compare_digest`, and `dev_token` entries are gated behind
   `RETAIL_INSIGHTS_ALLOW_DEV_TOKENS` (default `true` to keep the demo/tests working).
9. **Prompt-injection patterns too narrow** — `drop table users`, `delete from users`,
   `truncate users`, and `ignore the above instructions` all bypassed
   `src/security/prompt_injection.py`. Fixed with broader table-DDL/DML and ignore-instruction
   patterns.

## Minor

10. `_transient()` in `src/llm/client.py` and `_is_transient_error()` in
    `src/database/bigquery_runner.py` matched bare substrings like `"rate"` and `"500"`,
    which also match words like "generate" or arbitrary byte counts. Narrowed to
    phrase-level terms.
11. `provider_ladder` in `src/llm/client.py` listed a fallback model that was never actually
    added when it equaled the primary model. Fixed to use the same condition as the real
    fallback list.
12. `count_redactions()` in `src/security/pii_patterns.py` used `zip(before, after)`, which
    silently truncates on length mismatch. Changed to `zip(..., strict=True)`.
13. `GoldenBucket.load()` raised `KeyError` and aborted the whole bucket on one malformed
    trio YAML. Fixed to skip and record the file in `self.load_warnings`.
14. Dead `elif name == "pii_guard"` branch in the graph's `instrument()` wrapper — no node
    is named `pii_guard`, so `redactions_made` was computed but never logged on the real
    graph path. Fixed by logging it from the `executor` branch instead.
15. `ReportsStore` never exposed a way to close its SQLite connection. Added `close()`.

## Found during live smoke testing (real Gemini + real BigQuery)

16. **`sql_generator.generate_sql` treated the raw LLM completion as literal SQL** — no
    extraction of a markdown code fence, and no defense against the model continuing past
    the SQL and echoing the few-shot template's own `Analyst report style:` section back
    into the same completion. Live run (`Why is the Texas branch underperforming...`) hit
    this exactly: the real Gemini response appended leaked report-style commentary after a
    valid SQL statement, and `sqlglot` failed to parse the combined blob. Caught only by
    running the CLI live, not by the stub-LLM test suite. Fixed in
    `src/agent/nodes/sql_generator.py` with `_extract_sql()`, which strips a ` ```sql `
    fence if present, otherwise truncates at the first leaked template marker
    (`Analyst report style:` / `Business takeaway` / `Question:`); the prompt now also
    explicitly asks for SQL only, no commentary. Regression tests in
    `tests/unit/test_sql_generator.py`. Re-ran the same live question after the fix and
    got a correct end-to-end report from real Gemini + real BigQuery.

## Found during Docker portability check

17. **`docker-compose.yml` hardcoded a machine-specific, Windows-only host path**
    (`C:\retail\key.json`) in the `agent` and `evals` services' volume mounts, three times.
    This directly breaks the assignment's "must be runnable on another machine" requirement
    for the Docker path, and would also fail outright on Linux/Mac reviewers. Fixed by
    parameterizing it as `${GCP_KEY_HOST_PATH:-./secrets/key.json}`, documented in
    `.env.example`; `secrets/` added to `.gitignore`. `docs/HLD.md`'s "Mission fit" section
    also claimed "no Docker" despite a working `Dockerfile`/`docker-compose.yml` already being
    present in the repo — corrected to describe both the native and containerized paths.
    Verified by building all four compose services fresh and running the credential-free
    paths end to end in containers: `docker compose run --rm tests` (60 passed) and
    `docker compose run --rm evals-mock` (10/10 = 100%).

## Found while completing Hybrid Intelligence (Golden Bucket) compliance

18. **Golden Bucket "Analyst Report" trios were never used to guide report generation** —
    `reporter.generate_report()` had no `few_shots` parameter at all; the retrieved
    Question→SQL→Report trios only ever reached `sql_generator.generate_sql()`. This
    undercut the brief's "Hybrid Intelligence" requirement, which frames the Golden Bucket
    as teaching the agent "how analysts previously interpreted questions," not just how to
    write SQL. Fixed by threading `few_shots` through `generate_report()` (both the
    imperative `_run_analysis_pipeline` and the graph's `reporter_node`) and into the real-LLM
    prompt; `DeterministicStubLLM.generate_report` accepts and intentionally ignores it to
    keep CI output deterministic. Also rewrote all 15 seed trios' `report` fields in
    `data/golden_bucket/*.yaml` from terse instructions (e.g. "Rank brands by sales.") into
    short human-analyst-style takeaways, since the originals didn't read as "Analyst Report"
    examples at all. Verified live: asking "What is revenue by product category?" now
    produces a report that leads with revenue concentration framing, mirroring the rewritten
    `gb_002` trio almost verbatim.

19. **`router_node` leaked per-turn-conditional state fields across turns in the same
    persistent LangGraph thread** — found by chance during the live verification above.
    `router_node` returned `{**state, **route}`; since the SqliteSaver checkpointer restores
    the *entire* previous state for a thread before each new turn, and `route_intent()` only
    conditionally emits `branch_disclosure`/`branch_interpretation`/`unsupported_reason`, a
    later turn whose route doesn't set these kept whatever a prior turn in the same thread
    had left behind. Confirmed live: asking the Texas/California branch question and then an
    unrelated category-revenue question in the same default (stable, non-`--new-thread`) CLI
    session caused the branch disclaimer to wrongly prepend itself to the unrelated report.
    Other conditionally-set fields (`pending_confirmation`, `error`, `empty_explanation`,
    etc.) were audited and found safe — each is always freshly overwritten by the node that
    consumes it before it can leak into a visible response. Fixed by resetting the three
    leak-prone keys to `""` in `router_node` before merging the fresh route on top. Verified
    live on the already-tainted thread: the same category question no longer carries the
    stale disclaimer, and the fix is exercised by a new regression test on the real graph
    runtime (not just the imperative fallback, which was never affected).

## Found while verifying High-Stakes Oversight (destructive delete confirmation)

20. **Cross-user delete-confirmation hijack — CONFIRMED EXPLOITABLE, HIGH severity.**
    `delete_executor_node`'s owner check compared the checkpointed `state["user_id"]` against
    *itself* (`confirm_delete(..., state["user_id"], ...)`), never against the actual caller
    resuming the delete. Combined with three other facts, this let any authenticated user
    delete a *different* user's reports:
    - CLI thread IDs are predictable and caller-suppliable: `retail-insights:<user_id>`
      (`src/cli.py:_default_thread_id`), and `--thread-id` is a free-form override with no
      check that it belongs to `--user-id`.
    - The subset-delete confirmation token is a fixed, non-secret string
      (`CONFIRM_SUBSET_TOKEN = "CONFIRM DELETE"`), not a per-session nonce.
    - `cli.py`'s restart-resume fast path (`if raw in {CONFIRM_SUBSET_TOKEN,
      CONFIRM_ALL_TOKEN}: agent.resume_delete(None, raw, thread_id=thread_id)`) will resume
      *whatever* thread_id it's given, with no local `pending` object required.

    Reproduced live: `manager_a` requested a delete (pending interrupt left unconfirmed);
    `manager_b`, authenticated as themselves, called `resume_delete(None, "CONFIRM DELETE",
    thread_id="retail-insights:manager_a")` and successfully soft-deleted `manager_a`'s
    report.

    **Fix:** `RetailInsightsAgent.resume_delete()` now stamps the real caller's identity onto
    the resume via `Command(resume=user_input, update={"resuming_user_id": self.user_id})`
    (LangGraph 1.2.7's `Command` supports `update=` alongside `resume=`). `delete_executor_node`
    rejects with `"Cancelled. Confirmation owner did not match the current user."` whenever
    `resuming_user_id != user_id` (the checkpointed original owner), before ever calling
    `confirm_delete()`. Verified live: the same attack now returns
    `{"deleted": 0, "cancelled": True, ...}` and the report survives.

    **Known secondary limitation, not a security gap:** LangGraph's `interrupt()` is
    single-use, so a *blocked* hijack attempt still consumes the pending confirmation - the
    legitimate owner would see their own correct token rejected afterward and need to re-issue
    the delete request for a fresh prompt. Nothing is ever wrongly deleted; this is a UX rough
    edge stemming from interrupt-consumption semantics, considered out of scope for this pass.

## Found while verifying Resilience & Graceful Error Handling

21. **`BigQueryRunner.execute()` reports a misleading attempt count on failure** — the final
    fallback `return QueryResult(..., metadata={"attempts": 3})` hardcoded `3` regardless of
    how many attempts actually ran. A non-transient error breaks out of the retry loop on the
    first attempt, so the metadata claimed "3 attempts" when only 1 real attempt was made -
    misleading telemetry for anyone debugging "did this retry or fail fast?" from the logs.
    Found via a live test with a fake client that fails permanently. Fixed:
    `metadata={"attempts": attempt + 1}`, using the loop variable's true final value.

    No other gaps were found in this area - all verified live and passing on first try:
    - Cost cap: an over-budget query is rejected at the dry-run stage with zero rows
      materialized, never reaching a "real" (billable) execution.
    - Self-healing genuinely recovers mid-budget, not just exhausts it: a fake LLM whose
      first `generate_sql` call fails at execution and second call succeeds produces a real
      report from the corrected query after exactly one healing attempt.
    - `RetryLLM` retries transient errors (429/rate-limit/quota/timeout/503/temporary) with
      backoff until success or budget exhaustion, and fails fast (one attempt) on permanent
      errors like an invalid API key - it does not waste retries on errors retrying can't fix.
    - `BigQueryRunner.execute()` has the same transient-vs-permanent retry behavior.
    - The CLI's outer per-turn `try/except` genuinely survives an unexpected exception from
      deep inside `agent.answer()` and keeps answering subsequent questions in the same
      session, rather than crashing the process.

## Regression tests added/extended

- `tests/unit/test_pii_patterns.py` — formatted phones still masked; bare large numbers are not.
- `tests/unit/test_router_intents.py` — schema-mentioning delete requests still route to delete.
- `tests/unit/test_auth.py` — sha256-hashed tokens authenticate; dev tokens can be disabled.
- `tests/unit/test_self_healer.py` — exhaustion message includes the real error.
- `tests/unit/test_prompt_injection_refusal.py` — wider destructive-SQL phrasing is refused;
  benign analytics questions are not.
- `tests/unit/test_golden_bucket_semantic.py` (new) — query/document embeddings for identical
  text now agree; local vector search finds seeded trios without falling back to lexical
  search; malformed trio files are skipped instead of crashing `load()`.
- `tests/integration/test_graph_smoke.py` — unsupported email action returns the
  router-specific reason through the real graph; an execution error surfaces as a graceful
  failure with the real error text and does not persist a report.
- `tests/unit/test_sql_generator.py` (new) — markdown-fenced SQL is unwrapped; leaked
  few-shot report-style text is trimmed off; multi-part content blocks are joined; already
  clean SQL passes through unchanged.
- `tests/unit/test_reporter_preferences.py` — the deterministic stub accepts `few_shots`
  without error; a fake real-LLM's captured prompt actually contains the retrieved Golden
  Bucket trio text.
- `tests/integration/test_graph_smoke.py` — a branch-disclosure turn followed by an
  unrelated turn in the *same* persistent thread no longer leaks the disclaimer into the
  second report.
- `tests/integration/test_graph_smoke.py::test_cross_user_cannot_resume_another_users_pending_delete`
  (new) — a different authenticated user resuming someone else's thread with the correct
  static token cannot delete their reports.
- `tests/integration/test_learning_loop.py` (new) — `/feedback` → `promote_trio.py` refuses
  without `--reviewed`, succeeds with it, and a fresh `GoldenBucket` instance retrieves the
  promoted trio; an already-running bucket does not live-reload.
- `tests/integration/test_graph_smoke.py::test_self_healer_recovers_mid_budget_not_just_exhausts`
  (new) — self-healing genuinely produces a corrected, successful report, not just a
  graceful-failure message.
- `tests/unit/test_llm_client.py` (new) — `RetryLLM` recovers from transient errors with
  backoff, exhausts its budget correctly, and fails fast on permanent errors.
- `tests/unit/test_bigquery_runner.py` (new) — `BigQueryRunner.execute()` recovers from
  transient errors, fails fast on permanent ones with an accurate attempt count, and the mock
  runner's cost cap rejects an over-budget query before materializing rows.
- `tests/integration/test_cli_resilience.py` (new) — the CLI survives an unexpected
  exception mid-turn and keeps answering subsequent questions in the same session.

## Verification

```text
python -m pytest tests/ -q
75 passed

python scripts/verify_runtime.py
Runtime verification OK: real LangGraph + SqliteSaver + BigQuery deps are importable and pinned.

python evaluation/run_evals.py --mode mock
Mode: mock
Pass rate: 10/10 = 100%
```

## Explicitly not changed

- The PII column denylist (`email|phone|street_address|postal_code`) still does not cover
  `first_name`, `last_name`, `city`, `latitude`, `longitude`. This looks like an intentional
  scope decision matching `config/schema_notes.yaml`, not a bug, but it is worth a deliberate
  follow-up decision rather than a silent widening.
- `promote_trio.py`'s index rebuild only reindexes the `--out` directory; harmless on the
  default path, not touched here.
