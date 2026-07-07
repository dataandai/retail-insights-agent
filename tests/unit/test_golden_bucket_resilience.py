"""Golden Bucket must survive embedding-provider failures (e.g. 429 quota exhaustion).

Found live: free-tier Gemini embedding quota ran out and the whole agent crashed at
startup inside GoldenBucket seeding instead of degrading. Startup now rebuilds the index
with the deterministic embedder; mid-session query failures fall back to lexical ranking.
"""
import os

os.environ.setdefault("USE_STUB_LLM", "true")
os.environ.setdefault("USE_MOCK_BQ", "true")

from src.knowledge.golden_bucket import DeterministicEmbedding, GoldenBucket


class _AlwaysFailingEmbedding:
    dims = 768

    def embed(self, text: str, *, task_type: str) -> list[float]:
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")


class _FailsAfterSeedingEmbedding(DeterministicEmbedding):
    """Seeds documents fine, then starts failing on queries (mid-session quota loss)."""

    def __init__(self):
        self.fail = False

    def embed(self, text: str, *, task_type: str) -> list[float]:
        if self.fail:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return super().embed(text, task_type=task_type)


def test_seeding_failure_degrades_to_deterministic_embedding_not_crash():
    bucket = GoldenBucket(embedding=_AlwaysFailingEmbedding())
    assert "deterministic fallback" in bucket.degraded_reason
    assert isinstance(bucket.embedding, DeterministicEmbedding)
    # Retrieval still works in the rebuilt vector space.
    assert bucket.search("Who are our top customers by total spend?")


def test_query_time_embedding_failure_falls_back_to_lexical():
    embedding = _FailsAfterSeedingEmbedding()
    bucket = GoldenBucket(embedding=embedding)
    assert bucket.degraded_reason == ""
    embedding.fail = True
    results = bucket.search("Which product categories have the highest return rate?")
    assert results, "lexical fallback must still ground the question"
