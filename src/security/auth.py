"""Local-first CLI authentication for owner-scoped operations.

The assignment prototype is a CLI, not a web service, so there is no SSO session to
trust. This module makes the trust boundary explicit: the self-declared --user-id must be
paired with a local token before the CLI can act as that user. Production deployments
should replace this file-backed check with enterprise SSO / IAM, but the local prototype no
longer treats arbitrary --user-id input as authenticated identity.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    reason: str = ""


DEFAULT_USERS_PATH = Path("config/users.yaml")


def _load_users(path: str | Path = DEFAULT_USERS_PATH) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"users": {}}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {"users": {}}


def _dev_tokens_allowed() -> bool:
    """Plaintext dev_token entries are demo-only; set RETAIL_INSIGHTS_ALLOW_DEV_TOKENS=false
    to reject them without editing users.yaml."""
    return os.getenv("RETAIL_INSIGHTS_ALLOW_DEV_TOKENS", "true").lower() != "false"


def _candidate_tokens(user_id: str, cfg: dict[str, Any]) -> set[str]:
    user_cfg = (cfg.get("users") or {}).get(user_id) or {}
    tokens: set[str] = set()
    env_name = user_cfg.get("token_env")
    if env_name and os.getenv(str(env_name)):
        tokens.add(os.getenv(str(env_name), ""))
    if user_cfg.get("dev_token") and _dev_tokens_allowed():
        tokens.add(str(user_cfg["dev_token"]))
    global_env = f"RETAIL_INSIGHTS_TOKEN_{user_id.upper().replace('-', '_')}"
    if os.getenv(global_env):
        tokens.add(os.getenv(global_env, ""))
    return {t for t in tokens if t}


def _matches_token_hash(user_id: str, supplied: str, cfg: dict[str, Any]) -> bool:
    user_cfg = (cfg.get("users") or {}).get(user_id) or {}
    expected = user_cfg.get("token_sha256")
    if not expected:
        return False
    digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, str(expected).lower())


def verify_cli_user(user_id: str, token: str | None, *, users_path: str | Path = DEFAULT_USERS_PATH) -> AuthResult:
    """Verify that a CLI caller is allowed to operate as user_id.

    Set RETAIL_INSIGHTS_AUTH_REQUIRED=false only for throwaway demos. Tests and evals
    instantiate the agent directly, but the interactive CLI should never accept a bare
    --user-id as proof of identity.
    """
    if os.getenv("RETAIL_INSIGHTS_AUTH_REQUIRED", "true").lower() == "false":
        return AuthResult(True)
    supplied = token or os.getenv("RETAIL_INSIGHTS_USER_TOKEN") or os.getenv("RETAIL_MANAGER_TOKEN")
    if not supplied:
        return AuthResult(False, "missing auth token; pass --auth-token or set RETAIL_INSIGHTS_USER_TOKEN")
    cfg = _load_users(users_path)
    users = cfg.get("users") or {}
    if user_id not in users:
        return AuthResult(False, f"unknown user_id '{user_id}' in local auth config")
    allowed = _candidate_tokens(user_id, cfg)
    if any(hmac.compare_digest(supplied, t) for t in allowed) or _matches_token_hash(user_id, supplied, cfg):
        return AuthResult(True)
    return AuthResult(False, "invalid auth token for requested user_id")
