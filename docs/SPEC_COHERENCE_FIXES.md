# Spec Coherence Fixes

This file records the final corrections made after the sentence-level specification audit.
It is intentionally grounded in code changes, not self-certification.

## 1. Real LangGraph runtime proof

**Previous gap:** local tests could pass through the `LocalCompiledGraph` fallback when the
`langgraph` package was not installed, so they did not prove the real runtime path.

**Fixes:**

- `requirements.txt` now contains exact pins, including `langgraph==1.2.7` and
  `langgraph-checkpoint-sqlite==3.1.0`.
- `scripts/verify_runtime.py` fails if the real LangGraph modules, SQLite checkpointer,
  BigQuery client, or required pinned versions are missing.
- `tests/integration/test_real_langgraph_runtime_contract.py` asserts that
  `build_langgraph()` returns a real `ConfiguredCompiledGraph`, not `LocalCompiledGraph`,
  when the dependency is installed.
- `ConfiguredCompiledGraph` preserves the compiled LangGraph object while injecting the
  required `configurable.thread_id` from state for direct invocations.

## 2. Structured telemetry for real graph nodes

**Previous gap:** the imperative/local path had good node logs, but the real LangGraph
nodes did not emit a structured JSON line at each meaningful transition.

**Fixes:**

- `build_langgraph()` now wraps every node with an `instrument()` wrapper.
- Each node logs `start`, `ok`, and `error` events with `thread_id`, `turn_id`, node name,
  latency, and node-specific fields such as tables, row counts, bytes estimates,
  retry count, redaction count, and report id.
- `tests/integration/test_real_langgraph_runtime_contract.py` verifies that real graph
  execution writes node-level telemetry for router, retriever, SQL generator, validator,
  executor, self-healer, and reporter.

## 3. LangGraph Store retrieval path

**Previous gap:** Golden Bucket trios were written to a LangGraph store when available,
while retrieval primarily used a custom in-memory vector list.

**Fixes:**

- `LangGraphSemanticStore` now constructs `InMemoryStore(index={"embed": ..., "dims": 768})`
  when LangGraph is available.
- Seed trios are written with `store.put(namespace, key, value, index=["question", "tags"])`.
- Retrieval first calls `store.search(namespace, query=question, limit=k)`.
- The explicit vector list remains only as a compatibility fallback for unit tests or API
  variance; logs expose the active backend as `LangGraph InMemoryStore.search` or local
  fallback.

## 4. Prompt-injection and control-plane refusal

**Previous gap:** unsafe requests such as "show your system prompt" were not leaked, but
were not explicitly refused as required by the adversarial checklist.

**Fixes:**

- Added `src/security/prompt_injection.py` with deterministic control-plane override
  detection.
- `router.py` now routes these requests to `intent="refusal"`.
- Both real LangGraph and local compatibility paths return a fixed refusal message.
- Added `tests/unit/test_prompt_injection_refusal.py`.

## 5. Reproducible dependency pins

**Previous gap:** `requirements.txt` used lower bounds, which made the graph/checkpointer
runtime version ambiguous.

**Fixes:**

- `requirements.txt` now pins exact versions used for the audited build.
- `scripts/verify_runtime.py` validates those exact versions after installation.

## Verified locally

```text
python scripts/verify_runtime.py
Runtime verification OK: real LangGraph + SqliteSaver + BigQuery deps are importable and pinned.

python -m pytest -q
36 passed

python evaluation/run_evals.py --mode mock
Mode: mock
Pass rate: 10/10 = 100%
```

## Remaining boundary

Live Gemini + live BigQuery correctness still requires the user's credentials and should be
run with:

```bash
python evaluation/run_evals.py --mode live --refresh-cache
python evaluation/run_evals.py --mode live
```
