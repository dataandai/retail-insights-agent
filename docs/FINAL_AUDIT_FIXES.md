# Final Audit Fixes

This document records the last three architecture-hardening changes applied after the final roast.

## 0. Post-review fixes after deep audit

A later code-level review found three additional hardening items. They are now fixed, not merely documented:

- `SELECT *` and `SELECT alias.*` against allowed tables are rejected by `sql_guardrails.py`; `COUNT(*)` remains allowed. This closes the wildcard PII-projection bypass.
- The CLI no longer accepts a bare self-declared `--user-id`. `src/security/auth.py` validates a local token from `config/users.yaml` or environment variables before owner-scoped report operations can run. Production should replace this local file-backed gate with SSO/IAM.
- Real LangGraph runtime failures no longer silently fall back to a duplicate imperative pipeline. The local imperative shim is used only when the `langgraph` package is absent in minimal test environments; real graph errors are logged and surfaced gracefully.
- `SqliteSaver` no longer silently downgrades to `MemorySaver` in real installs. If `langgraph-checkpoint-sqlite` is missing, startup fails unless `ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS=true` is set explicitly.

Relevant files:

- `src/security/sql_guardrails.py`
- `src/security/auth.py`
- `config/users.yaml`
- `src/cli.py`
- `src/agent/graph.py`
- `tests/unit/test_auth.py`
- `tests/unit/test_sql_pii_projection.py`

## 1. CLI state persistence

The CLI no longer relies on process-local RAM for session continuity. The default `thread_id` is stable per manager (`retail-insights:<user-id>`), and `--new-thread` is required to intentionally start from a clean conversation. When LangGraph's SQLite checkpoint package is installed, `build_langgraph()` compiles with `SqliteSaver` backed by the same local SQLite file used by the app. Pending delete confirmations and the latest turn used by `/feedback` are also persisted in the `cli_session_state` SQLite table so a CLI process restart does not erase active confirmation state.

Relevant files:

- `src/agent/graph.py`
- `src/cli.py`
- `src/database/reports_store.py`
- `requirements.txt`

## 2. Physically isolated mock evaluation

`evaluation/run_evals.py --mode mock` now injects `DeterministicStubLLM` and `MockBigQueryRunner` directly instead of relying only on environment variables. It also installs a socket blocker in mock mode, so any accidental Gemini or BigQuery network call fails immediately. Mock expected-result files are treated as local fixtures, and reference SQL is executed only against the mock runner unless `--mode live` is explicitly selected.

Relevant files:

- `evaluation/run_evals.py`
- `tests/unit/test_mock_eval_isolated.py`
- `evaluation/expected_results/mock/*.json`

## 3. SQL-level PII projection prevention

PII protection now starts before BigQuery result materialization. The SQL generator prompt instructs the model never to project `email`, `phone`, `street_address`, or `postal_code`; the deterministic stub follows the same rule; and `sql_guardrails.py` rejects any SELECT projection that contains those PII/quasi-PII columns. Runtime row masking and final regex masking remain as defense-in-depth controls.

Relevant files:

- `config/schema_notes.yaml`
- `src/agent/nodes/sql_generator.py`
- `src/security/sql_guardrails.py`
- `src/llm/client.py`
- `tests/unit/test_sql_pii_projection.py`

## Verification

```bash
python -m pytest -q
python evaluation/run_evals.py --mode mock
```

Expected:

```text
32 passed
Mode: mock
Pass rate: 10/10 = 100%
```
