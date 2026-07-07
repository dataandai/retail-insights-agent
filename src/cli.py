from __future__ import annotations

import argparse
import shlex

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from src.agent.graph import RetailInsightsAgent
from src.agent.nodes.confirmation import CONFIRM_ALL_TOKEN, CONFIRM_SUBSET_TOKEN
from src.observability.logger import summarize_log
from src.security.auth import verify_cli_user


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Retail Insights Agent CLI")
    parser.add_argument("--user-id", default="demo_manager", help="Authenticated manager/user id for owner-scoped reports and preferences")
    parser.add_argument("--auth-token", default=None, help="Local auth token for --user-id. May also be set with RETAIL_INSIGHTS_USER_TOKEN.")
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Durable LangGraph thread id. Defaults to a stable per-user value so CLI restarts keep checkpointed state.",
    )
    parser.add_argument("--new-thread", action="store_true", help="Start a fresh thread instead of the durable per-user thread.")
    return parser


def _default_thread_id(user_id: str, new_thread: bool = False) -> str:
    if new_thread:
        import uuid

        return f"retail-insights:{user_id}:{uuid.uuid4()}"
    return f"retail-insights:{user_id}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    auth = verify_cli_user(args.user_id, args.auth_token)
    if not auth.ok:
        print(f"Authentication failed: {auth.reason}")
        return 2
    agent = RetailInsightsAgent(user_id=args.user_id)
    thread_id = args.thread_id or _default_thread_id(agent.user_id, args.new_thread)
    persisted = agent.reports.load_cli_session_state(thread_id=thread_id, owner_id=agent.user_id)
    pending: dict | None = persisted.get("pending_confirmation")
    last_turn: dict | None = persisted.get("last_turn")
    print(f"Retail Insights Agent CLI. user_id={agent.user_id}, thread_id={thread_id}. Type /help for commands, /exit to quit.")
    if pending:
        print("A pending delete confirmation was restored from SQLite. Reply with the exact token to continue, or anything else to cancel.")
    while True:
        try:
            raw = input("manager> ").strip()
            if not raw:
                continue
            if raw == "/exit":
                return 0
            if raw == "/help":
                print("Commands: /prefs format=bullets|table tone=..., /feedback good|bad reason, /stats, /schema, /thread, /exit")
                continue
            if raw == "/thread":
                print(f"Current durable thread_id: {thread_id}")
                continue
            if raw.startswith("/stats"):
                print(summarize_log())
                continue
            if raw.startswith("/schema"):
                result = agent.answer(raw, thread_id=thread_id)
                last_turn = {"question": raw, **result}
                agent.reports.save_cli_session_state(thread_id=thread_id, owner_id=agent.user_id, last_turn=last_turn)
                print(result["text"])
                continue
            if raw.startswith("/prefs"):
                parts = dict(token.split("=", 1) for token in shlex.split(raw)[1:] if "=" in token)
                print(agent.reports.update_preferences(agent.user_id, format=parts.get("format"), tone=parts.get("tone")))
                continue
            if raw.startswith("/feedback"):
                toks = shlex.split(raw)
                rating = toks[1] if len(toks) > 1 else "bad"
                note = " ".join(toks[2:])
                fid = agent.reports.add_feedback(
                    turn_id=(last_turn or {}).get("turn_id", "manual"),
                    user_id=agent.user_id,
                    rating=rating,
                    note=note,
                    question=(last_turn or {}).get("question", ""),
                    sql=(last_turn or {}).get("sql", ""),
                    report_text=(last_turn or {}).get("text", ""),
                )
                print(f"Saved feedback id={fid}")
                continue
            if pending:
                result = agent.resume_delete(pending, raw, thread_id=thread_id)
                pending = None
                agent.reports.clear_pending_confirmation(thread_id=thread_id, owner_id=agent.user_id)
                print(result["message"])
                continue
            # If the process was restarted after LangGraph persisted an interrupt but the
            # lightweight CLI pending payload was not present, let Command(resume=...) try
            # to continue the checkpointed graph for exact delete tokens.
            if raw in {CONFIRM_SUBSET_TOKEN, CONFIRM_ALL_TOKEN}:
                result = agent.resume_delete(None, raw, thread_id=thread_id)
                print(result["message"])
                continue
            result = agent.answer(raw, thread_id=thread_id)
            last_turn = {"question": raw, **result}
            pending = result.get("pending_confirmation")
            agent.reports.save_cli_session_state(thread_id=thread_id, owner_id=agent.user_id, pending_confirmation=pending, last_turn=last_turn)
            print(result["text"])
        except KeyboardInterrupt:
            print("\nBye.")
            return 0
        except Exception as exc:
            print(f"Sorry, I hit a recoverable error and kept the session alive: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
