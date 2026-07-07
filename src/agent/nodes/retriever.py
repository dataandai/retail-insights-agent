from __future__ import annotations

from src.knowledge.golden_bucket import GoldenBucket


def retrieve_examples(question: str, bucket: GoldenBucket) -> str:
    return bucket.few_shot_prompt(question, k=3)
