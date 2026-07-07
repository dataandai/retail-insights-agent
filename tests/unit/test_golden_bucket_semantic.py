from src.knowledge.golden_bucket import (
    GOLDEN_NAMESPACE,
    DeterministicEmbedding,
    GoldenBucket,
    LangGraphSemanticStore,
)


def test_query_and_document_embeddings_share_vector_space():
    emb = DeterministicEmbedding()
    query = emb.embed("monthly revenue trend", task_type="RETRIEVAL_QUERY")
    doc = emb.embed("monthly revenue trend", task_type="RETRIEVAL_DOCUMENT")
    similarity = sum(a * b for a, b in zip(query, doc))
    assert similarity > 0.99


def test_local_vector_search_finds_seeded_trio_without_lexical_fallback():
    emb = DeterministicEmbedding()
    store = LangGraphSemanticStore(emb)
    store.langgraph_store = None  # force the local compatibility vector index
    bucket = GoldenBucket(embedding=emb, store=store)
    assert bucket.trios, "seed trios expected in data/golden_bucket"
    question = bucket.trios[0].question
    query_embedding = emb.embed(question, task_type="RETRIEVAL_QUERY")
    hits = store.search(GOLDEN_NAMESPACE, question, query_embedding, k=3)
    assert hits
    assert hits[0].id == bucket.trios[0].id


def test_load_skips_malformed_trio(tmp_path):
    (tmp_path / "good.yaml").write_text(
        "question: Q\nsql: SELECT 1\nreport: R\ntags: []\n", encoding="utf-8"
    )
    (tmp_path / "broken.yaml").write_text("question: only a question\n", encoding="utf-8")
    bucket = GoldenBucket(tmp_path, embedding=DeterministicEmbedding())
    assert [t.id for t in bucket.trios] == ["good"]
    assert any("broken.yaml" in w for w in bucket.load_warnings)
