"""Strict runtime verification.

This script fails if the real LangGraph runtime, SQLite checkpointer, sqlglot, and
BigQuery packages required by the master prompt are not installed. It is separate from
mock unit tests so a local CI smoke run cannot be mistaken for a live-runtime proof.
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import sys

REQUIRED = {
    "langgraph": "1.2.7",
    "langgraph-checkpoint-sqlite": "3.1.0",
    "sqlglot": "30.12.0",
    "google-cloud-bigquery": "3.42.1",
}

MODULES = [
    "langgraph.graph",
    "langgraph.types",
    "langgraph.checkpoint.sqlite",
    "langgraph.store.memory",
    "sqlglot",
    "google.cloud.bigquery",
]


def main() -> int:
    problems: list[str] = []
    for dist, expected in REQUIRED.items():
        try:
            got = md.version(dist)
        except md.PackageNotFoundError:
            problems.append(f"missing distribution: {dist}")
            continue
        if got != expected:
            problems.append(f"{dist}: expected {expected}, got {got}")
    for module in MODULES:
        if importlib.util.find_spec(module) is None:
            problems.append(f"missing import module: {module}")
    if problems:
        print("Runtime verification FAILED:")
        for item in problems:
            print(f"- {item}")
        return 1
    print("Runtime verification OK: real LangGraph + SqliteSaver + BigQuery deps are importable and pinned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
