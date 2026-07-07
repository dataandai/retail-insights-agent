# Specification Coherence Report After Fixes

## Scope

This report verifies the implementation against the master build prompt after the final
spec-coherence fixes.

## Summary verdict

The repository is now coherent with the master prompt at prototype scope. The remaining
unverified item is live BigQuery/Gemini execution accuracy, which cannot be proven without
external credentials. All local/prototype requirements are covered by code and tests.

## Sentence-level requirement status

| Requirement area | Status | Implementation evidence |
|---|---:|---|
| CLI-only REPL, no web UI, no Docker | PASS | `src/cli.py`, no server/UI entrypoints |
| BigQuery thelook_ecommerce tables only | PASS | `src/security/sql_guardrails.py` allowlist and dataset/project validation |
| Golden Bucket few-shot grounding | PASS | 15 YAML trios in `data/golden_bucket`; `src/knowledge/golden_bucket.py` indexes and searches top 3 |
| LangGraph Store for Golden Bucket | PASS | `LangGraphSemanticStore` uses `InMemoryStore.put(..., index=["question", "tags"])` and `store.search(...)` first |
| Deterministic PII never reaches user surface | PASS | SQL projection block, wildcard block, runtime PII columns, row masking, final text regex |
| PII masked before LLM reporter prompt | PASS | `executor_node` applies `guard_rows()` before `reporter_node`; imperative path does the same |
| SQL parser validation before execution | PASS | `validate_sql()` uses `sqlglot`, rejects semicolons/multi-statement/non-SELECT/forbidden ops/bad tables/wildcards/PII projections |
| Safe BigQuery runner | PASS | `dry_run`, `maximum_bytes_billed`, `max_results`, schema introspection, transient retry in `bigquery_runner.py` |
| Branch/store ambiguity disclosed | PASS | `router.py` emits demand/supply mapping disclosure before analysis |
| Saved Reports library | PASS | SQLite reports table, soft deletes, preferences, feedback, audit in `reports_store.py` |
| Delete owner scope | PASS | `resolve_delete_scope()` filters `owner_id` in SQL before preview/delete |
| Strict confirmation flow | PASS | exact tokens, preview, expiry, stronger all-delete token in `confirmation.py` |
| LangGraph interrupt/resume deletion | PASS | `build_langgraph()` `confirm_delete_node` uses `interrupt()` and routes to `delete_executor_node`; `resume_delete()` uses `Command(resume=...)` |
| Persistent CLI graph state | PASS | `SqliteSaver` required for real graph; no silent MemorySaver downgrade except explicit test env flag |
| User preferences | PASS | `/prefs`, SQLite `user_preferences`, reporter prompt wiring |
| Feedback loop | PASS | `/feedback`, `feedback` table, `promote_trio.py --reviewed` promotion path |
| Hot-reload persona config | PASS | `PersonaLoader` mtime checks `config/persona.yaml` each turn |
| Self-healing on SQL error and empty result | PASS | `maybe_heal()`, max shared retry budget, data bounds explanation |
| LLM fallback ladder | PASS | Gemini primary, Gemini fallback, optional OpenRouter and Ollama in `llm/client.py` |
| Structured telemetry | PASS | real graph `instrument()` wrapper logs node transitions; JSONL logger and `/stats` |
| Mock eval isolated | PASS | `run_evals.py --mode mock` injects deterministic LLM and `MockBigQueryRunner`, blocks sockets |
| Live eval path | IMPLEMENTED / CREDENTIAL-DEPENDENT | `run_evals.py --mode live --refresh-cache` and expected-result cache path |
| Prompt-injection refusal | PASS | `prompt_injection.py`, router refusal intent, fixed refusal before SQL/data access |
| Dependency reproducibility | PASS | exact pins in `requirements.txt`, `scripts/verify_runtime.py` |

## Local verification performed

```text
python scripts/verify_runtime.py
Runtime verification OK: real LangGraph + SqliteSaver + BigQuery deps are importable and pinned.

USE_STUB_LLM=true USE_MOCK_BQ=true python -m pytest -q
42 passed

USE_STUB_LLM=true USE_MOCK_BQ=true python evaluation/run_evals.py --mode mock
Mode: mock
Pass rate: 10/10 = 100%
```

## Honest boundary

The artifact still cannot honestly claim live Gemini + live BigQuery correctness until the
user runs the live commands with valid credentials. The code path exists, but the local audit
only proves the deterministic, isolated path and real LangGraph runtime import/execution.
