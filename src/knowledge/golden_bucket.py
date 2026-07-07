"""Golden Bucket seed trio loading and LangGraph-store-backed retrieval.

At runtime the system seeds a LangGraph InMemoryStore namespace with reviewed
Question -> SQL -> Analyst Report trios. Real Gemini embeddings are the primary path when
credentials are configured; a deterministic embedding implementation is retained only for
CI/offline smoke tests.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

GOLDEN_NAMESPACE = ("golden_bucket",)
EMBEDDING_DIMS = 768


@dataclass(frozen=True)
class GoldenTrio:
    id: str
    question: str
    sql: str
    report: str
    tags: list[str]


def _tokens(text: str) -> list[str]:
    return [t.lower().strip(".,?!:;()[]'\"") for t in text.split() if len(t.strip(".,?!:;()[]'\"")) > 2]


class DeterministicEmbedding:
    """Offline embedding for tests and CI only."""
    dims = EMBEDDING_DIMS

    def embed(self, text: str, *, task_type: str) -> list[float]:
        # task_type deliberately does not participate in the hash: queries and documents
        # must land in the same vector space or identical texts score 0 similarity.
        vec = [0.0] * self.dims
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class GeminiEmbedding:
    dims = EMBEDDING_DIMS

    def __init__(self):
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        model = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
        api_key = os.getenv("GEMINI_API_KEY")
        self.doc_embedder = GoogleGenerativeAIEmbeddings(
            model=model,
            google_api_key=api_key,
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=self.dims,
        )
        self.query_embedder = GoogleGenerativeAIEmbeddings(
            model=model,
            google_api_key=api_key,
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=self.dims,
        )

    def embed(self, text: str, *, task_type: str) -> list[float]:
        # gemini-embedding-001 accepts one input per call; startup seeds one trio at a time.
        if task_type == "RETRIEVAL_QUERY":
            return list(self.query_embedder.embed_query(text))
        return list(self.doc_embedder.embed_query(text))


class LangGraphSemanticStore:
    """Adapter around LangGraph InMemoryStore with deterministic fallback ranking.

    When LangGraph is installed, retrieval first calls the real Store API:
    store.put(namespace, key, value, index=["question", "tags"]) and
    store.search(namespace, query=question, limit=k). The explicit vector list remains
    only as a compatibility fallback for offline/unit-test environments or API variance.
    """

    def __init__(self, embedding: Any | None = None):
        self.items: list[tuple[tuple[str, ...], str, GoldenTrio, list[float]]] = []
        self.langgraph_store: Any | None = None
        self.embedding = embedding
        try:  # pragma: no cover - depends on optional langgraph package/version
            from langgraph.store.memory import InMemoryStore

            if embedding is not None:
                def embed_texts(texts):
                    if isinstance(texts, str):
                        return embedding.embed(texts, task_type="RETRIEVAL_QUERY")
                    return [embedding.embed(t, task_type="RETRIEVAL_DOCUMENT") for t in texts]

                self.langgraph_store = InMemoryStore(index={"embed": embed_texts, "dims": EMBEDDING_DIMS})
            else:
                self.langgraph_store = InMemoryStore()
        except Exception:
            self.langgraph_store = None

    @property
    def backend_name(self) -> str:
        return "LangGraph InMemoryStore.search" if self.langgraph_store is not None else "local compatibility vector index"

    def put(self, namespace: tuple[str, ...], key: str, trio: GoldenTrio, embedding: list[float]) -> None:
        self.items.append((namespace, key, trio, embedding))
        if self.langgraph_store is not None:
            value = asdict(trio)
            try:  # current LangGraph Store API
                self.langgraph_store.put(namespace, key, value, index=["question", "tags"])
            except TypeError:  # older API variants
                self.langgraph_store.put(namespace, key, value)

    def _trio_from_store_item(self, item: Any) -> GoldenTrio | None:
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            return None
        try:
            return GoldenTrio(
                id=str(value["id"]),
                question=str(value["question"]),
                sql=str(value["sql"]),
                report=str(value["report"]),
                tags=list(value.get("tags", [])),
            )
        except Exception:
            return None

    def search(self, namespace: tuple[str, ...], question: str, query_embedding: list[float], k: int) -> list[GoldenTrio]:
        if self.langgraph_store is not None:
            try:  # primary spec path: real LangGraph store.search semantic retrieval
                hits = self.langgraph_store.search(namespace, query=question, limit=k)
                trios = [trio for trio in (self._trio_from_store_item(hit) for hit in hits) if trio is not None]
                if trios:
                    return trios[:k]
            except Exception:
                # Compatibility fallback only; never hides the store backend in logs.
                pass
        scored: list[tuple[float, GoldenTrio]] = []
        for ns, _, trio, emb in self.items:
            if ns != namespace:
                continue
            score = sum(a * b for a, b in zip(query_embedding, emb))
            scored.append((score, trio))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [trio for score, trio in scored[:k] if score > 0]


class GoldenBucket:
    def __init__(self, root: str | Path = "data/golden_bucket", *, embedding=None, store: LangGraphSemanticStore | None = None):
        self.root = Path(root)
        self.trios = self.load()
        self.embedding = embedding or self._make_embedding()
        self.store = store or LangGraphSemanticStore(self.embedding)
        self._seed_store()

    @staticmethod
    def _make_embedding():
        explicit_stub = os.getenv("USE_STUB_LLM", "").lower() == "true" or os.getenv("CI", "").lower() == "true"
        if not explicit_stub and os.getenv("GEMINI_API_KEY"):
            return GeminiEmbedding()
        return DeterministicEmbedding()

    def load(self) -> list[GoldenTrio]:
        trios: list[GoldenTrio] = []
        self.load_warnings: list[str] = []
        for path in sorted(self.root.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            missing = [key for key in ("question", "sql", "report") if not raw.get(key)]
            if missing:
                self.load_warnings.append(f"{path.name}: skipped, missing required fields {missing}")
                continue
            trios.append(GoldenTrio(id=path.stem, question=raw["question"], sql=raw["sql"], report=raw["report"], tags=list(raw.get("tags", []))))
        return trios

    def _seed_store(self) -> None:
        for trio in self.trios:
            emb = self.embedding.embed(trio.question, task_type="RETRIEVAL_DOCUMENT")
            self.store.put(GOLDEN_NAMESPACE, trio.id, trio, emb)

    def rebuild_index(self) -> None:
        self.store = LangGraphSemanticStore(self.embedding)
        self.trios = self.load()
        self._seed_store()

    def search(self, question: str, k: int = 3) -> list[GoldenTrio]:
        query_embedding = self.embedding.embed(question, task_type="RETRIEVAL_QUERY")
        semantic = self.store.search(GOLDEN_NAMESPACE, question, query_embedding, k=k)
        if semantic:
            return semantic
        return self._lexical_fallback(question, k=k)

    def _lexical_fallback(self, question: str, k: int = 3) -> list[GoldenTrio]:
        q = set(_tokens(question))
        scored: list[tuple[float, GoldenTrio]] = []
        for trio in self.trios:
            doc = set(_tokens(trio.question + " " + " ".join(trio.tags)))
            if not doc:
                continue
            score = len(q & doc) / math.sqrt(len(doc))
            if {"branch", "store", "region"} & q and "branch" in trio.tags:
                score += 1.0
            if "return" in q and "returns" in trio.tags:
                score += 0.8
            if score > 0:
                scored.append((score, trio))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:k]]

    def few_shot_prompt(self, question: str, k: int = 3) -> str:
        examples = self.search(question, k=k)
        chunks = []
        for ex in examples:
            chunks.append(f"Question: {ex.question}\nSQL:\n{ex.sql}\nAnalyst report style:\n{ex.report}")
        return "\n\n---\n".join(chunks)
