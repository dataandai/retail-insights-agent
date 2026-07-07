"""Persona agility: non-developer tone changes must apply without redeploy,
and a bad persona.yaml edit must never take a turn down."""
import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.agent.nodes.reporter import PersonaLoader, generate_report
from src.database.reports_store import ReportsStore
from src.llm.client import DeterministicStubLLM


def _bump_mtime(path):
    stat = path.stat()
    os.utime(path, (stat.st_atime + 2, stat.st_mtime + 2))


def test_persona_loader_hot_reloads_on_mtime_change(tmp_path):
    p = tmp_path / "persona.yaml"
    p.write_text("tone: concise_executive\n", encoding="utf-8")
    loader = PersonaLoader(p)
    assert loader.load()["tone"] == "concise_executive"
    p.write_text("tone: playful_marketing\n", encoding="utf-8")
    _bump_mtime(p)
    assert loader.load()["tone"] == "playful_marketing"


def test_persona_loader_keeps_last_good_config_on_broken_yaml(tmp_path):
    p = tmp_path / "persona.yaml"
    p.write_text("tone: playful_marketing\n", encoding="utf-8")
    events = []
    loader = PersonaLoader(p, on_event=lambda event, **f: events.append((event, f)))
    assert loader.load()["tone"] == "playful_marketing"
    p.write_text("tone: [unclosed\n  broken: {yaml", encoding="utf-8")
    _bump_mtime(p)
    assert loader.load()["tone"] == "playful_marketing"
    assert loader.last_error
    assert events[-1][0] == "reload_failed"


def test_persona_loader_treats_non_mapping_yaml_as_error(tmp_path):
    p = tmp_path / "persona.yaml"
    p.write_text("tone: playful_marketing\n", encoding="utf-8")
    loader = PersonaLoader(p)
    loader.load()
    p.write_text("just a plain sentence, not a mapping\n", encoding="utf-8")
    _bump_mtime(p)
    assert loader.load()["tone"] == "playful_marketing"
    assert "mapping" in loader.last_error


def test_persona_loader_recovers_after_yaml_is_fixed(tmp_path):
    p = tmp_path / "persona.yaml"
    p.write_text("tone: a\n", encoding="utf-8")
    loader = PersonaLoader(p)
    loader.load()
    p.write_text("tone: [broken", encoding="utf-8")
    _bump_mtime(p)
    loader.load()
    p.write_text("tone: b\n", encoding="utf-8")
    _bump_mtime(p)
    _bump_mtime(p)
    assert loader.load()["tone"] == "b"
    assert loader.last_error == ""


def test_get_preferences_returns_empty_for_user_who_never_set_prefs(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    assert store.get_preferences("fresh_user") == {}


def test_update_preferences_stores_only_the_axis_the_user_set(tmp_path):
    store = ReportsStore(tmp_path / "reports.sqlite3")
    assert store.update_preferences("u1", format="table") == {"format": "table"}
    assert store.get_preferences("u1") == {"format": "table"}


def test_persona_tone_applies_when_user_never_set_preferences():
    """Regression: hardcoded preference defaults used to mask persona.yaml entirely,
    making the CEO's weekly tone change dead configuration."""
    report = generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        DeterministicStubLLM(),
        {},
        {"tone": "playful_marketing", "format_defaults": {"preferred_format": "bullets"}},
    )
    assert "playful_marketing" in report


def test_explicit_user_preference_still_overrides_persona():
    report = generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        DeterministicStubLLM(),
        {"tone": "urgent"},
        {"tone": "playful_marketing"},
    )
    assert "urgent" in report
