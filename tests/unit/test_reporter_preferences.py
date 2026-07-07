from src.agent.nodes.reporter import generate_report
from src.llm.client import DeterministicStubLLM


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _PromptCapturingLLM:
    """Fake real-LLM (no generate_report method) that records the prompt it received."""

    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return _FakeMessage("Business takeaway: stub response")


def test_reporter_uses_user_table_preference():
    report = generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        DeterministicStubLLM(),
        {"format": "table", "tone": "plain"},
        {"format_defaults": {"preferred_format": "bullets"}},
    )
    assert "| category | revenue |" in report


def test_reporter_uses_user_tone():
    report = generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        DeterministicStubLLM(),
        {"format": "bullets", "tone": "urgent"},
        {},
    )
    assert "urgent" in report


def test_reporter_forwards_few_shots_to_stub_without_error():
    # The deterministic stub must accept (and may ignore) few_shots without raising.
    report = generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        DeterministicStubLLM(),
        {"format": "bullets", "tone": "plain"},
        {},
        few_shots="Question: revenue by category\nSQL:\nSELECT 1\nAnalyst report style:\nlead with concentration",
    )
    assert report


def test_reporter_includes_golden_bucket_few_shots_in_real_llm_prompt():
    llm = _PromptCapturingLLM()
    generate_report(
        "revenue",
        [{"category": "Jeans", "revenue": 10.0}],
        llm,
        {"format": "bullets", "tone": "plain"},
        {},
        few_shots="Question: What is revenue by product category?\nSQL:\nSELECT ...\nAnalyst report style:\nRevenue is concentrated in a handful of categories.",
    )
    assert "Revenue is concentrated in a handful of categories" in llm.last_prompt
    assert "Prior human-analyst trios" in llm.last_prompt
