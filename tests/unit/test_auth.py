import hashlib

from src.security.auth import verify_cli_user


def test_cli_auth_rejects_impersonation_without_token(monkeypatch):
    monkeypatch.delenv("RETAIL_INSIGHTS_USER_TOKEN", raising=False)
    result = verify_cli_user("manager_a", None)
    assert not result.ok
    assert "missing auth token" in result.reason


def test_cli_auth_accepts_matching_local_token(monkeypatch):
    monkeypatch.delenv("RETAIL_INSIGHTS_USER_TOKEN", raising=False)
    result = verify_cli_user("manager_a", "manager-a-token")
    assert result.ok


def test_cli_auth_rejects_wrong_user_token(monkeypatch):
    monkeypatch.delenv("RETAIL_INSIGHTS_USER_TOKEN", raising=False)
    result = verify_cli_user("manager_b", "manager-a-token")
    assert not result.ok


def test_cli_auth_sha256_hashed_token(tmp_path, monkeypatch):
    monkeypatch.delenv("RETAIL_INSIGHTS_USER_TOKEN", raising=False)
    token = "analyst-secret-token"
    users = tmp_path / "users.yaml"
    users.write_text(
        f"users:\n  analyst:\n    token_sha256: {hashlib.sha256(token.encode()).hexdigest()}\n",
        encoding="utf-8",
    )
    assert verify_cli_user("analyst", token, users_path=users).ok
    assert not verify_cli_user("analyst", "wrong-token", users_path=users).ok


def test_cli_auth_dev_token_can_be_disabled(monkeypatch):
    monkeypatch.delenv("RETAIL_INSIGHTS_USER_TOKEN", raising=False)
    monkeypatch.delenv("MANAGER_A_TOKEN", raising=False)
    monkeypatch.setenv("RETAIL_INSIGHTS_ALLOW_DEV_TOKENS", "false")
    result = verify_cli_user("manager_a", "manager-a-token")
    assert not result.ok
