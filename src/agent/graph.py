"""LangGraph-first Retail Insights Agent orchestration."""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from src.agent.nodes.confirmation import PendingConfirmation, confirmation_prompt, confirm_delete, make_pending_confirmation, resolve_scope
from src.agent.nodes.executor import execute_sql
from src.agent.nodes.pii_guard import guard_report, guard_rows
from src.agent.nodes.reporter import PersonaLoader, generate_report
from src.agent.nodes.retriever import retrieve_examples
from src.agent.nodes.router import route_intent
from src.agent.nodes.self_healer import MAX_HEALING_ATTEMPTS, maybe_heal
from src.agent.nodes.sql_generator import generate_sql
from src.agent.nodes.sql_validator import validate_generated_sql
from src.database.bigquery_runner import Runner, make_runner, reconcile_schema_with_notes
from src.database.reports_store import ReportsStore
from src.knowledge.golden_bucket import GoldenBucket
from src.llm.client import make_llm
from src.observability.llm_trace import maybe_trace_llm
from src.observability.logger import JsonlLogger, current_turn
from src.security.pii_patterns import configure_pii_columns, count_redactions, runtime_pii_columns
from src.security.prompt_injection import REFUSAL_MESSAGE
from src.security.sql_guardrails import ALLOWED_TABLES, extract_tables

TIMESTAMP_COLUMNS = ("created_at", "returned_at", "shipped_at", "delivered_at")


class LocalCompiledGraph:
    """LangGraph-compatible fallback used only when langgraph is not installed."""
    is_local_fallback = True

    def __init__(self, agent: "RetailInsightsAgent"):
        self.agent = agent

    def invoke(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.agent._answer_imperative(
            state["question"],
            thread_id=state.get("thread_id"),
            turn_id=state.get("turn_id"),
            _from_graph=True,
        )




class ConfiguredCompiledGraph:
    """Thin wrapper that preserves LangGraph while supplying thread config by default.

    LangGraph checkpointers require config["configurable"]["thread_id"]. The public
    build_langgraph() helper should be safe to invoke directly with a state that already
    carries thread_id, so this wrapper injects the config without changing the graph
    semantics or hiding runtime errors.
    """

    is_local_fallback = False

    def __init__(self, compiled: Any):
        self._compiled = compiled

    def invoke(self, state_or_command: Any, config: dict[str, Any] | None = None) -> Any:
        if config is None:
            thread_id = "default"
            if isinstance(state_or_command, dict):
                thread_id = str(state_or_command.get("thread_id") or state_or_command.get("user_id") or "default")
            config = {"configurable": {"thread_id": thread_id}}
        elif "configurable" not in config or "thread_id" not in config.get("configurable", {}):
            thread_id = "default"
            if isinstance(state_or_command, dict):
                thread_id = str(state_or_command.get("thread_id") or state_or_command.get("user_id") or "default")
            config = {**config, "configurable": {**config.get("configurable", {}), "thread_id": thread_id}}
        return self._compiled.invoke(state_or_command, config=config)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._compiled, name)


class RetailInsightsAgent:
    def __init__(self, *, runner: Runner | None = None, store: ReportsStore | None = None, user_id: str = "demo_manager", llm: Any | None = None):
        self.runner = runner or make_runner()
        self.reports = store or ReportsStore()
        self.bucket = GoldenBucket()
        self.log = JsonlLogger()
        self.llm = maybe_trace_llm(llm or make_llm(), self.log)
        self.persona_loader = PersonaLoader(
            on_event=lambda event, **fields: self.log.event(
                thread_id=current_turn.get()[0], turn_id=current_turn.get()[1], node="persona_loader", event=event, **fields
            )
        )
        self.user_id = user_id
        self.schema = self.runner.introspect_schema()
        configure_pii_columns(self.schema)
        self.schema_warnings = reconcile_schema_with_notes(self.schema)
        self._compiled_graph: Any | None = None
        for warning in self.schema_warnings:
            self.log.event(thread_id="startup", turn_id="schema", node="schema_reconcile", event="warning", warning=warning)
        self.log.event(thread_id="startup", turn_id="schema", node="pii_runtime_columns", event="ok", columns=sorted(runtime_pii_columns()))

    def _load_schema_notes(self) -> dict[str, Any]:
        path = Path("config/schema_notes.yaml")
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _schema_answer(self) -> str:
        notes = self._load_schema_notes()
        lines = ["Available tables and columns from live startup introspection:"]
        for table, cols in self.schema.items():
            lines.append(f"- {table}: {', '.join(cols)}")
        lines.append("\nRuntime PII columns masked before any LLM prompt:")
        pii_cols = sorted(runtime_pii_columns())
        lines.append("- " + (", ".join(pii_cols) if pii_cols else "none found in live schema; regex net still active"))
        branch = notes.get("business_terms", {}).get("branch", {}) or notes.get("branch_mapping", {})
        if branch:
            lines.append("\nBranch/store/region mapping:")
            lines.append("- Demand-side default: users.state / users.country, treating customer geography as the branch proxy.")
            lines.append("- Supply-side option: distribution_centers joined through products.distribution_center_id.")
            lines.append("- The dataset is online-only; no physical branch column exists.")
        lines.append("\nKnown schema drift note:")
        lines.append("- orders.num_of_item vs orders.num_of_items is re-verified at startup; either spelling is accepted only if live schema confirms it.")
        if self.schema_warnings:
            lines.append("\nSchema-note reconciliation warnings:")
            lines.extend(f"- {w}" for w in self.schema_warnings)
        return "\n".join(lines)

    def _data_bounds_for_sql(self, sql: str) -> dict[str, Any]:
        try:
            tables = extract_tables(sql)
        except Exception:
            tables = set()
        if not tables:
            tables = {"orders" if "orders" in sql.lower() else "order_items"}
        lower = sql.lower()
        mentioned_cols = [c for c in TIMESTAMP_COLUMNS if c in lower] or ["created_at"]
        ranges: list[dict[str, Any]] = []
        for table in sorted(tables & ALLOWED_TABLES):
            for column in mentioned_cols:
                if column in self.schema.get(table, []):
                    b = self.runner.timestamp_bounds(table, column)
                    ranges.append({"table": b.table, "column": b.column, "min_value": b.min_value, "max_value": b.max_value})
        if not ranges:
            b = self.runner.timestamp_bounds("order_items", "created_at")
            ranges.append({"table": b.table, "column": b.column, "min_value": b.min_value, "max_value": b.max_value})
        first = ranges[0]
        return {**first, "ranges": ranges}

    def _run_analysis_pipeline(self, question: str, *, thread_id: str, turn_id: str, route: dict[str, Any], started: float) -> dict[str, Any]:
        q = question
        retries = 0
        final_sql = ""
        rows: list[dict[str, Any]] = []
        error = ""
        validation: dict[str, Any] = {}
        empty_explanation = ""

        while True:
            few_shots = retrieve_examples(q, self.bucket)
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="retriever", event="ok", has_examples=bool(few_shots), store_backend=self.bucket.store.backend_name)
            sql = generate_sql(q, few_shots, self.llm)
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="sql_generator", event="ok")
            validation = validate_generated_sql(sql)
            final_sql = validation["sql"]
            self.log.event(
                thread_id=thread_id,
                turn_id=turn_id,
                node="sql_validator",
                event="ok" if validation["validation_ok"] else "reject",
                reason=validation.get("validation_error"),
                tables=validation.get("tables"),
                sql=final_sql,
            )
            if not validation["validation_ok"]:
                error = validation["validation_error"]
                heal = maybe_heal(q, final_sql, error, False, retries, self.llm)
                if heal.get("should_retry"):
                    retries = int(heal["retries"])
                    self.log.event(thread_id=thread_id, turn_id=turn_id, node="self_healer", event="retry", retries=retries, reason=heal.get("reason"), error=error)
                    q = heal["question"]
                    continue
                text = f"I could not run a safe query: {error}"
                self.log.event(thread_id=thread_id, turn_id=turn_id, node="turn_summary", event="graceful_failure", latency_ms=round((time.time() - started) * 1000, 2), healing_attempts=retries)
                return {"text": text, "sql": final_sql, "error": error, "healing_attempts": retries, "turn_id": turn_id, "intent": route.get("intent")}

            executed = execute_sql(final_sql, self.runner)
            rows_before_guard = executed["rows"]
            rows = guard_rows(rows_before_guard)
            redactions = count_redactions(rows_before_guard, rows)
            error = executed.get("error", "")
            empty = bool(executed.get("empty"))
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="executor", event="error" if error else "ok", bytes_estimate=executed.get("bytes_estimate"), rows=len(rows), error=error)
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="pii_guard", event="ok", redactions_made=redactions, runtime_columns=sorted(runtime_pii_columns()))
            data_bounds = self._data_bounds_for_sql(final_sql) if empty else None
            heal = maybe_heal(q, final_sql, error, empty, retries, self.llm, data_bounds=data_bounds)
            if heal.get("should_retry"):
                retries = int(heal["retries"])
                self.log.event(thread_id=thread_id, turn_id=turn_id, node="self_healer", event="retry", retries=retries, reason=heal.get("reason"), error=error, empty=empty)
                q = heal["question"]
                continue
            empty_explanation = heal.get("message", "") if empty else ""
            break

        prefs = self.reports.get_preferences(self.user_id)
        persona = self.persona_loader.load()
        report = generate_report(question, rows, self.llm, prefs, persona, empty_explanation=empty_explanation, few_shots=few_shots)
        if route.get("branch_disclosure"):
            report = route["branch_disclosure"] + "\n\n" + report
        if route.get("intent") == "pii_sensitive_analysis":
            report = "I cannot reveal customer email, phone, address, or other PII. Requested PII fields are [REDACTED] and are not projected in SQL output; the analysis below is redacted before reporting.\n\n" + report
        report = guard_report(report)
        report_id = self.reports.save_report(owner_id=self.user_id, question=question, sql=final_sql, report_text=report, tags=",".join(validation.get("tables", [])))
        latency = round((time.time() - started) * 1000, 2)
        self.log.event(thread_id=thread_id, turn_id=turn_id, node="reporter", event="ok", latency_ms=latency, report_id=report_id, healing_attempts=retries)
        self.log.event(thread_id=thread_id, turn_id=turn_id, node="turn_summary", event="ok", latency_ms=latency, report_id=report_id, healing_attempts=retries, intent=route.get("intent"))
        return {"text": report, "sql": final_sql, "rows": rows, "report_id": report_id, "healing_attempts": retries, "retries": retries, "intent": route.get("intent"), "turn_id": turn_id}

    def _answer_imperative(self, question: str, *, thread_id: str | None = None, turn_id: str | None = None, _from_graph: bool = False) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        turn_id = turn_id or str(uuid.uuid4())
        current_turn.set((thread_id, turn_id))
        started = time.time()
        self.log.event(thread_id=thread_id, turn_id=turn_id, node="router", event="start", question=question, user_id=self.user_id)
        route = route_intent(question)
        intent = route["intent"]
        self.log.event(thread_id=thread_id, turn_id=turn_id, node="router", event="ok", intent=intent, branch_interpretation=route.get("branch_interpretation"))

        if intent == "refusal":
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="refusal", event="ok", reason="prompt_injection_or_control_override")
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="turn_summary", event="refused", latency_ms=round((time.time() - started) * 1000, 2), intent=intent)
            return {"text": REFUSAL_MESSAGE, "intent": intent, "turn_id": turn_id}

        if intent == "schema":
            text = self._schema_answer()
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="schema", event="ok")
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="turn_summary", event="ok", latency_ms=round((time.time() - started) * 1000, 2), intent=intent)
            return {"text": text, "intent": intent, "turn_id": turn_id}

        if intent == "delete_report":
            scope = resolve_scope(question, self.user_id, self.reports)
            pending = make_pending_confirmation(scope, self.user_id)
            payload = confirmation_prompt(scope, self.user_id)
            text = (
                f"Matched {payload['count']} owner-scoped report(s). Preview: {payload['preview']}\n"
                f"Type exactly `{payload['token']}` to delete. Any other reply cancels. This expires automatically."
            )
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="confirmation", event="pending", count=payload["count"], blast_radius=payload["blast_radius"])
            return {"text": text, "intent": intent, "pending_confirmation": pending.to_dict(), "turn_id": turn_id, "thread_id": thread_id}

        return self._run_analysis_pipeline(question, thread_id=thread_id, turn_id=turn_id, route=route, started=started)

    def _compiled(self):
        if self._compiled_graph is None:
            self._compiled_graph = build_langgraph(self)
        return self._compiled_graph

    def _state_to_result(self, out: dict[str, Any], *, thread_id: str, turn_id: str) -> dict[str, Any]:
        if not isinstance(out, dict):
            return {"text": str(out), "thread_id": thread_id, "turn_id": turn_id}
        # LangGraph interrupt output shape varies slightly across versions.
        interrupt_obj = out.get("__interrupt__") or out.get("interrupt") or out.get("graph_interrupt")
        if interrupt_obj:
            payload = interrupt_obj
            if isinstance(interrupt_obj, (list, tuple)) and interrupt_obj:
                payload = getattr(interrupt_obj[0], "value", interrupt_obj[0])
            if not isinstance(payload, dict):
                payload = {"action": "delete_reports", "payload": str(payload)}
            token = payload.get("token", "CONFIRM DELETE")
            text = f"Matched {payload.get('count', 0)} owner-scoped report(s). Preview: {payload.get('preview', [])}\nType exactly `{token}` to delete. Any other reply cancels."
            return {"text": text, "intent": "delete_report", "pending_confirmation": payload, "thread_id": thread_id, "turn_id": turn_id, "langgraph_interrupt": True}
        if "report" in out:
            return {
                "text": out.get("text") or out.get("report", ""),
                "sql": out.get("sql", ""),
                "rows": out.get("rows", []),
                "report_id": out.get("report_id"),
                "healing_attempts": out.get("retries", 0),
                "intent": out.get("intent"),
                "thread_id": thread_id,
                "turn_id": turn_id,
            }
        if "text" in out:
            return out
        return {"text": out.get("answer_text", ""), "thread_id": thread_id, "turn_id": turn_id, **out}

    def answer(self, question: str, *, thread_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        turn_id = turn_id or str(uuid.uuid4())
        graph = self._compiled()
        # The only supported non-LangGraph execution path is the explicit LocalCompiledGraph
        # used when the langgraph package is absent in a minimal test environment. In real
        # installs, graph runtime failures must be visible and logged, not silently routed to
        # a second hand-maintained imperative implementation with different semantics.
        if isinstance(graph, LocalCompiledGraph):
            out = graph.invoke({"thread_id": thread_id, "turn_id": turn_id, "user_id": self.user_id, "question": question, "original_question": question, "retries": 0})
            return self._state_to_result(out, thread_id=thread_id, turn_id=turn_id)
        started = time.time()
        try:
            state = {"thread_id": thread_id, "turn_id": turn_id, "user_id": self.user_id, "question": question, "original_question": question, "retries": 0}
            out = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
            result = self._state_to_result(out, thread_id=thread_id, turn_id=turn_id)
            # The imperative path logs its own turn_summary; the real LangGraph path
            # must emit one too, or /stats has no turn-level metrics in real installs.
            outcome = "pending_confirmation" if result.get("langgraph_interrupt") else ""
            if not outcome and isinstance(out, dict):
                outcome = out.get("turn_outcome") or ""
            self.log.event(
                thread_id=thread_id,
                turn_id=turn_id,
                node="turn_summary",
                event=outcome or "ok",
                latency_ms=round((time.time() - started) * 1000, 2),
                intent=result.get("intent"),
                healing_attempts=(out.get("retries", 0) if isinstance(out, dict) else 0),
                report_id=result.get("report_id"),
            )
            return result
        except Exception as exc:
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="langgraph_runtime", event="error", error=str(exc))
            self.log.event(thread_id=thread_id, turn_id=turn_id, node="turn_summary", event="runtime_error", latency_ms=round((time.time() - started) * 1000, 2), error=str(exc))
            return {
                "text": "I could not complete this turn through the LangGraph runtime. The session is still alive, but no fallback pipeline was executed.",
                "error": str(exc),
                "thread_id": thread_id,
                "turn_id": turn_id,
            }

    def resume_delete(self, pending: dict[str, Any] | None, user_input: str, *, thread_id: str | None = None) -> dict[str, Any]:
        graph = self._compiled()
        if not isinstance(graph, LocalCompiledGraph):
            try:  # pragma: no cover - requires real langgraph runtime
                from langgraph.types import Command
                # Stamp the actual caller's identity onto the resumed state so
                # delete_executor_node can verify it against the checkpointed owner - thread_id
                # is caller-suppliable and predictable, so resuming must not implicitly trust it.
                out = graph.invoke(
                    Command(resume=user_input, update={"resuming_user_id": self.user_id}),
                    config={"configurable": {"thread_id": thread_id or "default"}},
                )
                result = out.get("delete_result", {}) if isinstance(out, dict) else {}
                deleted = result.get("deleted", out.get("deleted", 0) if isinstance(out, dict) else 0)
                cancelled = result.get("cancelled", out.get("cancelled", False) if isinstance(out, dict) else False)
                message = result.get("message", out.get("report", out.get("text", "Done.")) if isinstance(out, dict) else "Done.")
                self.log.event(
                    thread_id=thread_id or "unknown",
                    turn_id="resume",
                    node="turn_summary",
                    event="delete_cancelled" if cancelled else "delete_executed",
                    deleted=deleted,
                )
                return {"deleted": deleted, "cancelled": bool(cancelled), "message": message}
            except Exception as exc:
                self.log.event(thread_id=thread_id or "unknown", turn_id="resume", node="langgraph_resume", event="error", error=str(exc))
                return {"deleted": 0, "cancelled": True, "message": "Cancelled. The LangGraph delete confirmation could not be resumed; nothing was deleted."}
        if pending and pending.get("ids"):
            return confirm_delete(PendingConfirmation.from_dict(pending), user_input, self.user_id, self.reports)
        return {"deleted": 0, "cancelled": True, "message": "Cancelled. No active delete confirmation was found."}


def _make_persistent_checkpointer(sqlite_path: Path):
    """Return a persistent LangGraph checkpointer backed by the local SQLite DB.

    In a real LangGraph installation this must be SqliteSaver; silently downgrading to
    MemorySaver would reintroduce the CLI amnesia bug. MemorySaver is permitted only
    when ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS=true is set explicitly.
    """
    try:  # pragma: no cover - exercised when langgraph-checkpoint-sqlite is installed
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(str(sqlite_path), check_same_thread=False)
        saver = SqliteSaver(conn)
        setattr(saver, "is_persistent_sqlite", True)
        return saver
    except Exception as exc:
        if os.getenv("ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS", "false").lower() == "true":
            from langgraph.checkpoint.memory import MemorySaver

            saver = MemorySaver()
            setattr(saver, "is_persistent_sqlite", False)
            return saver
        raise RuntimeError(
            "langgraph-checkpoint-sqlite is required for CLI persistence. "
            "Install requirements.txt or set ALLOW_IN_MEMORY_CHECKPOINTER_FOR_TESTS=true only in tests."
        ) from exc


def build_langgraph(agent: RetailInsightsAgent | None = None):
    """Build the required LangGraph StateGraph with interrupt/resume deletion."""
    agent = agent or RetailInsightsAgent()
    try:
        from langgraph.graph import END, StateGraph
        from langgraph.types import interrupt
        from src.agent.state import AgentState
    except Exception:
        return LocalCompiledGraph(agent)

    def router_node(state: AgentState) -> AgentState:
        route = route_intent(state["question"])
        # The persistent SqliteSaver checkpointer keeps the whole thread's last state
        # around across turns. route_intent only conditionally emits these three keys, so
        # without an explicit reset a prior turn's branch disclaimer (or unsupported-action
        # reason) would silently keep attaching itself to every later, unrelated turn in the
        # same thread. Reset them here; the fresh route below overrides when it does set them.
        # report_id is also reset: it is only set by reporter, so a non-report turn
        # (refusal/schema/delete) would otherwise echo the previous turn's id in its
        # turn_summary and mislead a debugging session.
        reset = {"branch_disclosure": "", "branch_interpretation": "", "unsupported_reason": "", "turn_outcome": "", "report_id": None}
        return {**state, **reset, **route}

    def refusal_node(state: AgentState) -> AgentState:
        return {**state, "report": REFUSAL_MESSAGE, "text": REFUSAL_MESSAGE, "turn_outcome": "refused"}

    def unsupported_node(state: AgentState) -> AgentState:
        text = state.get("unsupported_reason") or "This CLI prototype does not support that action. Use /help for supported commands."
        return {**state, "report": text, "text": text, "turn_outcome": "unsupported"}

    def schema_node(state: AgentState) -> AgentState:
        answer = agent._schema_answer()
        return {**state, "report": answer, "text": answer, "turn_outcome": "ok"}

    def resolve_delete_node(state: AgentState) -> AgentState:
        scope = resolve_scope(state["question"], state["user_id"], agent.reports)
        pending = make_pending_confirmation(scope, state["user_id"])
        # Keep checkpoint state JSON-serializable. The pure DeleteScope dataclass is
        # intentionally not persisted; it is safe to recompute before the interrupt.
        return {**state, "pending_confirmation": pending.to_dict()}

    def confirm_delete_node(state: AgentState) -> AgentState:
        pending = PendingConfirmation.from_dict(state["pending_confirmation"])
        payload = {"action": "delete_reports", "owner_id": pending.owner_id, "count": len(pending.ids), "preview": pending.preview, "token": pending.token, "blast_radius": pending.blast_radius, "expires_at": pending.expires_at}
        token = interrupt(payload)
        return {**state, "confirmation_token": str(token)}

    def delete_executor_node(state: AgentState) -> AgentState:
        # thread_id is caller-suppliable and predictable (retail-insights:<user_id>), and the
        # subset/all-reports confirmation tokens are fixed, non-secret strings - so without
        # this check, any authenticated user who supplies a *different* user's thread_id could
        # resume and execute *their* pending delete. resuming_user_id is stamped by
        # RetailInsightsAgent.resume_delete() from the actual caller's identity, independent of
        # whatever user_id was checkpointed when the delete was originally requested.
        owner_id = state["user_id"]
        resuming_user_id = state.get("resuming_user_id")
        if resuming_user_id != owner_id:
            message = "Cancelled. Confirmation owner did not match the current user."
            return {**state, "delete_result": {"deleted": 0, "cancelled": True, "message": message}, "report": message, "text": message, "deleted": 0, "cancelled": True, "turn_outcome": "delete_cancelled"}
        result = confirm_delete(PendingConfirmation.from_dict(state["pending_confirmation"]), state.get("confirmation_token", ""), owner_id, agent.reports)
        outcome = "delete_cancelled" if result.get("cancelled") else "delete_executed"
        return {**state, "delete_result": result, "report": result["message"], "text": result["message"], "deleted": result.get("deleted", 0), "cancelled": result.get("cancelled", False), "turn_outcome": outcome}

    def retriever_node(state: AgentState) -> AgentState:
        return {**state, "few_shots": retrieve_examples(state["question"], agent.bucket)}

    def sql_generator_node(state: AgentState) -> AgentState:
        return {**state, "sql": generate_sql(state["question"], state.get("few_shots", ""), agent.llm)}

    def validator_node(state: AgentState) -> AgentState:
        validation = validate_generated_sql(state["sql"])
        return {**state, **validation, "error": validation.get("validation_error", "") if not validation.get("validation_ok") else state.get("error", "")}

    def executor_node(state: AgentState) -> AgentState:
        result = execute_sql(state["sql"], agent.runner)
        before = result["rows"]
        rows = guard_rows(before)
        return {**state, "rows": rows, "error": result.get("error", ""), "empty": result.get("empty", False), "bytes_estimate": result.get("bytes_estimate", 0), "redactions_made": count_redactions(before, rows)}

    def healer_node(state: AgentState) -> AgentState:
        retries = int(state.get("retries", 0))
        data_bounds = agent._data_bounds_for_sql(state.get("sql", "")) if state.get("empty") else None
        heal = maybe_heal(state["question"], state.get("sql", ""), state.get("error", ""), bool(state.get("empty")), retries, agent.llm, data_bounds=data_bounds)
        if heal.get("should_retry"):
            return {**state, "question": heal["question"], "retries": heal["retries"], "needs_retry": True, "error": "", "empty": False}
        return {**state, "needs_retry": False, "empty_explanation": heal.get("message", "") if state.get("empty") else ""}

    def graceful_failure_node(state: AgentState) -> AgentState:
        error = state.get("error") or state.get("validation_error") or "unknown error"
        text = f"I could not run a safe query: {error}"
        return {**state, "report": text, "text": text, "turn_outcome": "graceful_failure"}

    def reporter_node(state: AgentState) -> AgentState:
        prefs = agent.reports.get_preferences(state["user_id"])
        report = generate_report(state.get("original_question", state["question"]), state.get("rows", []), agent.llm, prefs, agent.persona_loader.load(), empty_explanation=state.get("empty_explanation", ""), few_shots=state.get("few_shots", ""))
        if state.get("branch_disclosure"):
            report = state["branch_disclosure"] + "\n\n" + report
        if state.get("intent") == "pii_sensitive_analysis":
            report = "I cannot reveal customer email, phone, address, or other PII. Requested PII fields are [REDACTED] and are not projected in SQL output; the analysis below is redacted before reporting.\n\n" + report
        report = guard_report(report)
        report_id = agent.reports.save_report(owner_id=state["user_id"], question=state.get("original_question", state["question"]), sql=state.get("sql", ""), report_text=report, tags=",".join(state.get("tables", [])))
        return {**state, "report": report, "text": report, "report_id": report_id, "turn_outcome": "ok"}

    def route_after_router(state: dict[str, Any]) -> str:
        if state.get("intent") == "refusal":
            return "refusal"
        if state.get("intent") == "schema":
            return "schema"
        if state.get("intent") == "unsupported_action":
            return "unsupported"
        if state.get("intent") == "delete_report":
            return "resolve_delete"
        return "retriever"

    def route_after_validator(state: dict[str, Any]) -> str:
        return "self_healer" if not state.get("validation_ok") else "executor"

    def route_after_healer(state: dict[str, Any]) -> str:
        if state.get("needs_retry") and int(state.get("retries", 0)) <= MAX_HEALING_ATTEMPTS:
            return "retriever"
        # Validation/execution errors must surface as a graceful failure without saving a
        # report, matching the imperative path. Empty-but-successful results still report.
        if not state.get("validation_ok") or state.get("error"):
            return "graceful_failure"
        return "reporter"

    def instrument(name: str, fn):
        def wrapped(state: AgentState) -> AgentState:
            start = time.time()
            thread_id = state.get("thread_id", "unknown")
            turn_id = state.get("turn_id", "unknown")
            current_turn.set((thread_id, turn_id))
            start_fields: dict[str, Any] = {}
            if name == "router":
                # The question and caller identity anchor the whole turn's message
                # correspondence in the log; without them a debugging session cannot
                # even tell what was asked.
                start_fields = {"question": state.get("question"), "user_id": state.get("user_id")}
            agent.log.event(thread_id=thread_id, turn_id=turn_id, node=name, event="start", **start_fields)
            try:
                out = fn(state)
                fields: dict[str, Any] = {"latency_ms": round((time.time() - start) * 1000, 2)}
                if name == "router":
                    fields["intent"] = out.get("intent")
                    fields["branch_interpretation"] = out.get("branch_interpretation")
                elif name == "retriever":
                    fields["has_examples"] = bool(out.get("few_shots"))
                    fields["store_backend"] = agent.bucket.store.backend_name
                elif name == "sql_generator":
                    fields["sql"] = out.get("sql")
                    # After a self-heal retry the healer rewrites the question; log the
                    # variant this SQL was generated from, not just the original.
                    fields["question_variant"] = out.get("question")
                elif name == "sql_validator":
                    fields["validation_ok"] = out.get("validation_ok")
                    fields["validation_error"] = out.get("validation_error")
                    fields["tables"] = out.get("tables")
                    fields["sql"] = out.get("sql")
                elif name == "executor":
                    fields["rows"] = len(out.get("rows", []))
                    fields["bytes_estimate"] = out.get("bytes_estimate")
                    fields["error"] = out.get("error")
                    fields["redactions_made"] = out.get("redactions_made", 0)
                elif name == "self_healer":
                    fields["retries"] = out.get("retries", 0)
                    fields["needs_retry"] = out.get("needs_retry")
                    fields["error"] = state.get("error")
                    fields["empty"] = state.get("empty")
                elif name == "reporter":
                    fields["report_id"] = out.get("report_id")
                    fields["report_preview"] = (out.get("report") or "")[:240]
                    fields["persona_reload_error"] = agent.persona_loader.last_error or None
                agent.log.event(thread_id=thread_id, turn_id=turn_id, node=name, event="ok", **{k: v for k, v in fields.items() if v is not None or k in ("latency_ms",)})
                return out
            except Exception as exc:
                agent.log.event(thread_id=thread_id, turn_id=turn_id, node=name, event="error", latency_ms=round((time.time() - start) * 1000, 2), error=str(exc))
                raise
        return wrapped

    graph = StateGraph(AgentState)
    for name, node in {
        "router": router_node,
        "refusal": refusal_node,
        "schema": schema_node,
        "unsupported": unsupported_node,
        "resolve_delete": resolve_delete_node,
        "confirm_delete": confirm_delete_node,
        "delete_executor": delete_executor_node,
        "retriever": retriever_node,
        "sql_generator": sql_generator_node,
        "sql_validator": validator_node,
        "executor": executor_node,
        "self_healer": healer_node,
        "reporter": reporter_node,
        "graceful_failure": graceful_failure_node,
    }.items():
        graph.add_node(name, instrument(name, node))
    graph.set_entry_point("router")
    graph.add_conditional_edges("router", route_after_router, {"refusal": "refusal", "schema": "schema", "unsupported": "unsupported", "resolve_delete": "resolve_delete", "retriever": "retriever"})
    graph.add_edge("refusal", END)
    graph.add_edge("schema", END)
    graph.add_edge("unsupported", END)
    graph.add_edge("resolve_delete", "confirm_delete")
    graph.add_edge("confirm_delete", "delete_executor")
    graph.add_edge("delete_executor", END)
    graph.add_edge("retriever", "sql_generator")
    graph.add_edge("sql_generator", "sql_validator")
    graph.add_conditional_edges("sql_validator", route_after_validator, {"self_healer": "self_healer", "executor": "executor"})
    graph.add_edge("executor", "self_healer")
    graph.add_conditional_edges("self_healer", route_after_healer, {"retriever": "retriever", "reporter": "reporter", "graceful_failure": "graceful_failure"})
    graph.add_edge("reporter", END)
    graph.add_edge("graceful_failure", END)
    compiled = graph.compile(checkpointer=_make_persistent_checkpointer(agent.reports.path), store=getattr(agent.bucket.store, "langgraph_store", None))
    return ConfiguredCompiledGraph(compiled)
