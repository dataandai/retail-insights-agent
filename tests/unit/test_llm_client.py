from src.llm.client import RetryLLM


class _Msg:
    def __init__(self, content):
        self.content = content


class _FlakyTransient:
    def __init__(self, fail_times: int):
        self.calls = 0
        self.fail_times = fail_times

    def invoke(self, prompt: str):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise Exception("429 rate limit exceeded")
        return _Msg("ok")


class _AlwaysFailPermanent:
    def __init__(self):
        self.calls = 0

    def invoke(self, prompt: str):
        self.calls += 1
        raise Exception("invalid API key")


def test_retry_llm_recovers_from_transient_errors():
    inner = _FlakyTransient(fail_times=2)
    llm = RetryLLM(inner, retries=2)
    result = llm.invoke("test")
    assert result.content == "ok"
    assert inner.calls == 3


def test_retry_llm_gives_up_after_exhausting_budget():
    inner = _FlakyTransient(fail_times=5)
    llm = RetryLLM(inner, retries=2)
    try:
        llm.invoke("test")
        assert False, "should have raised after exhausting the retry budget"
    except Exception as exc:
        assert "429" in str(exc)
    assert inner.calls == 3  # 1 initial + 2 retries, never unbounded


def test_retry_llm_does_not_retry_permanent_errors():
    inner = _AlwaysFailPermanent()
    llm = RetryLLM(inner, retries=2)
    try:
        llm.invoke("test")
        assert False, "should have raised"
    except Exception as exc:
        assert "invalid API key" in str(exc)
    assert inner.calls == 1, "a non-transient error must fail fast, not waste retries"
