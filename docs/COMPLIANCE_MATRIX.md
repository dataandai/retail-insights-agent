# Compliance Matrix — Master Build Prompt

This file maps the implementation back to the master specification so reviewers can inspect the repo quickly.

| Spec area | Implementation |
|---|---|
| CLI only, no web UI / Docker | `src/cli.py`; README clean setup uses only pip and `.env` |
| External services | Real mode requires BigQuery and Gemini key; local mode uses mock BigQuery and stub LLM |
| PII before LLM | `sql_generator.py` instruction + `sql_guardrails.py` PII/wildcard projection reject + `BigQueryRunner.execute`, `guard_rows`, final `guard_report` |
| SQL parser validation | `src/security/sql_guardrails.py` with `sqlglot`; rejects semicolons, DDL/DML, wrong dataset, unknown tables, wildcard projections, and user-facing PII projections |
| Safe BigQuery runner | `src/database/bigquery_runner.py`: `dry_run`, `MAX_BYTES_BILLED`, `maximum_bytes_billed`, `max_results`, transient retry |
| Schema introspection and reconciliation | `BigQueryRunner.introspect_schema`; `reconcile_schema_with_notes`; startup warnings in JSONL logs |
| Branch problem | `config/schema_notes.yaml`, `router.py`, HLD section; demand-side default and supply-side distribution center path |
| Golden Bucket | 15 YAML trios; `GoldenBucket` seeded once into in-memory semantic index; `promote_trio.py` human promotion path |
| LangGraph topology | `build_langgraph()` with `StateGraph`, required persistent SQLite checkpointer via `SqliteSaver` in real installs, conditional retry loop, interrupt/resume delete executor; no silent imperative fallback on graph runtime errors |
| Delete confirmation | `confirmation.py`: side-effect-free scope resolution, exact token, all-reports stronger token, expiration, owner-scoped soft delete |
| Local CLI auth + preferences and feedback | `config/users.yaml` token gate prevents bare `--user-id` impersonation; SQLite `user_preferences`, `/prefs`, `/feedback` tied to persisted last turn question/SQL; pending confirmations stored in SQLite for CLI restarts |
| Persona hot reload | `PersonaLoader` mtime-checks `config/persona.yaml` on report generation; precedence explicit `/prefs` > persona > built-in default; invalid/non-mapping YAML keeps last-good config and logs `reload_failed` |
| Self-healing | `maybe_heal` handles errors and empty rows with shared `MAX_HEALING_ATTEMPTS=2` |
| Observability | `JsonlLogger` writes payload-carrying per-node events and one `turn_summary` per turn on the real graph path; `/stats` aggregates turn outcomes, error rate, p95 latency, real self-heal retries; `LLM_TRACE=true` logs prompt/response pairs |
| Evaluation | `evaluation/golden_cases.json` + `run_evals.py` check reference execution, no PII, expected tables, branch disclosure, masking, empty result handling |
| Tests | Unit tests for PII, SQL guardrails, confirmation, self-healer, reporter preferences; integration smoke tests |

## Local verification

```bash
python -m pytest -q
python evaluation/run_evals.py
```

Expected at the time of this package:

```text
92 passed
Pass rate: 10/10 = 100%
```

See `docs/BUGFIX_AUDIT_2026-07-07.md` for the round of correctness/security fixes and
regression tests that brought the count from 42 to 75 (including a live-only SQL-generation
parsing bug, a cross-turn state-leak bug, and a confirmed-exploitable cross-user
delete-confirmation hijack, all found by driving the CLI end to end rather than only reading
the code).

## Final spec-coherence audit addendum

| Audit item | Status | Evidence |
|---|---:|---|
| Real LangGraph runtime can be proven when dependencies are installed | PASS | `requirements.txt` exact pins; `scripts/verify_runtime.py`; `tests/integration/test_real_langgraph_runtime_contract.py` |
| No silent fallback from real LangGraph runtime to a second imperative implementation | PASS | `RetailInsightsAgent.answer()` returns a controlled runtime error on real graph failure; `LocalCompiledGraph` is used only when `langgraph` import is unavailable |
| Real graph node-transition telemetry | PASS | `instrument()` wrapper in `src/agent/graph.py`; log assertion in integration test |
| Golden Bucket retrieval through LangGraph Store search | PASS | `LangGraphSemanticStore.search()` first calls `self.langgraph_store.search(namespace, query=question, limit=k)` |
| Explicit prompt-injection refusal | PASS | `src/security/prompt_injection.py`, router `intent="refusal"`, unit tests |
| Dependency reproducibility | PASS | exact pins in `requirements.txt` and runtime verification script |
| Live BigQuery/Gemini execution accuracy | EXTERNAL | code path implemented; requires user's credentials to execute |
| Extensibility for new capabilities (charts, email) and new data sources | PASS | `docs/HLD.md` "Extensibility" section: intent-branch pattern for new nodes, interrupt/confirm reuse for side-effecting actions, `Runner` Protocol + namespaced Golden Bucket store for new sources |
| Evaluation scope boundary explicitly documented | PASS | `docs/HLD.md` "Assumptions and trade-offs": structural/execution-signature checks, not semantic report-quality grading |
| Runnable on another machine (Docker path) | PASS | `docker-compose.yml` no longer hardcodes a machine-specific host key path (`${GCP_KEY_HOST_PATH:-./secrets/key.json}`); verified with `docker compose run --rm tests` and `docker compose run --rm evals-mock` |
| Golden Bucket "Analyst Report" trios actually shape report generation, not just SQL generation | PASS | `reporter.generate_report()` now takes `few_shots`, threaded from both the imperative pipeline and `reporter_node`; seed trio `report` fields rewritten as human-analyst takeaways; verified live (category-revenue report mirrors the rewritten `gb_002` trio's framing) |
| No cross-turn state leakage in the persistent LangGraph thread | PASS | `router_node` resets `branch_disclosure`/`branch_interpretation`/`unsupported_reason` every turn before merging the fresh route; found and verified live (a stale branch disclaimer no longer leaks into an unrelated later report in the same thread) |
| Users can only delete their own reports (not merely owner-scoped SQL, but the confirmation/resume flow itself) | PASS | Found and fixed a confirmed-exploitable cross-user delete hijack: `delete_executor_node` now verifies the actual resuming caller's identity (`Command(update={"resuming_user_id": ...})`) against the checkpointed owner, not just the checkpoint's own stored `user_id` against itself. See `docs/BUGFIX_AUDIT_2026-07-07.md` #20 |
| Continuous Improvement — System Level learning loop | PASS | Verified live end-to-end: `/feedback` → `promote_trio.py` refuses without `--reviewed` (exit code 4, no file written) → succeeds with `--reviewed` (YAML written, index rebuilt) → a fresh `GoldenBucket` instance retrieves the promoted trio for a similar future question. Documented nuance: unlike `persona.yaml`'s mtime hot reload, an already-running agent's bucket does not live-reload — promotion takes effect on the next process start. `tests/integration/test_learning_loop.py` |
| Resilience & Graceful Error Handling | PASS | Verified live: cost cap rejects an over-budget query before materializing rows; self-healing genuinely recovers mid-budget (not just exhausts); `RetryLLM` and `BigQueryRunner` both retry transient errors with backoff and fail fast on permanent ones; the CLI's outer try/except survives an unexpected mid-turn exception and keeps answering. Found and fixed a misleading attempt-count bug in `BigQueryRunner.execute()`'s failure path (see #21). `tests/unit/test_llm_client.py`, `tests/unit/test_bigquery_runner.py`, `tests/integration/test_cli_resilience.py` |
| Observability — agent-level metrics and deep-dive message-correspondence debugging | PASS | Found and fixed live: the real LangGraph path emitted no `turn_summary` and no message payloads (question/intent/SQL/report), and `/stats` inflated self-heal counts 4×. Now every turn is reconstructable from `logs/agent.jsonl` by `thread_id`/`turn_id` (question → intent → SQL → validation → rows → masked report preview → outcome), `/stats` reports turn outcomes/error rate/p95 latency/real retries, and `LLM_TRACE=true` logs PII-masked prompt/response pairs per LLM call. See `docs/OBSERVABILITY_PERSONA_AUDIT_2026-07-07.md` #24–#25. `tests/unit/test_observability.py`, `tests/integration/test_observability_and_persona_graph.py` |
| Agility — non-developers change agent tone without redeployment | PASS | Found and fixed live: hardcoded preference defaults masked `persona.yaml` entirely (the CEO's tone change never applied — dead config), and a broken YAML edit mid-session failed the turn. Now precedence is explicit `/prefs` > `persona.yaml` > built-in default, hot reload verified live mid-session, and invalid/non-mapping YAML keeps the last good persona while logging `persona_loader: reload_failed`. See `docs/OBSERVABILITY_PERSONA_AUDIT_2026-07-07.md` #22–#23. `tests/unit/test_persona_agility.py`, `tests/integration/test_observability_and_persona_graph.py` |
