"""Tests for API-based RAG components and integration points."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Dict, List

from app.rag.document_processor import DocumentChunk, chunk_document, chunk_text, load_docx, load_pdf
from app.rag.embedding_client import EmbeddingClient, GeminiAPIError
from app.rag.knowledge_base import KnowledgeBase, get_knowledge_base, init_knowledge_base
from app.rag.vector_store import VectorStore


class _DummyRedis:
    def __init__(self):
        self._store: Dict[str, str] = {}

    def get(self, key: str):
        return self._store.get(key)

    def setex(self, key: str, _ttl: int, value: str):
        self._store[key] = value
        return True


class _InMemoryDBManager:
    def __init__(self):
        self.rows: List[Dict[str, object]] = []
        self._next_id = 1

    def execute_query(self, query: str, params=None, dictionary=False):
        _ = dictionary
        norm = " ".join(query.lower().split())
        params = params or ()

        if "select distinct doc_id from knowledge_chunks where doc_name = %s" in norm:
            doc_name = params[0]
            doc_ids = sorted({row["doc_id"] for row in self.rows if row["doc_name"] == doc_name})
            return [(doc_id,) for doc_id in doc_ids]

        if "select count(*) from knowledge_chunks where doc_id = %s" in norm:
            doc_id = params[0]
            count = sum(1 for row in self.rows if row["doc_id"] == doc_id)
            return [(count,)]

        if "select id, doc_id, doc_name, chunk_index, content, embedding, metadata from knowledge_chunks" in norm:
            ordered = sorted(self.rows, key=lambda row: row["id"])
            return [
                (
                    row["id"],
                    row["doc_id"],
                    row["doc_name"],
                    row["chunk_index"],
                    row["content"],
                    row["embedding"],
                    row["metadata"],
                )
                for row in ordered
            ]

        if "select count(*) from knowledge_chunks" in norm:
            return [(len(self.rows),)]

        raise AssertionError(f"Unsupported query in test DB: {query}")

    def execute_many(self, query: str, params_list: List[tuple]) -> int:
        if "insert into knowledge_chunks" not in " ".join(query.lower().split()):
            raise AssertionError(f"Unsupported execute_many query: {query}")

        for params in params_list:
            doc_id, doc_name, chunk_index, content, embedding, metadata = params
            if isinstance(embedding, str):
                embedding = bytes.fromhex(embedding)
            self.rows.append(
                {
                    "id": self._next_id,
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "chunk_index": int(chunk_index),
                    "content": content,
                    "embedding": embedding,
                    "metadata": metadata,
                }
            )
            self._next_id += 1
        return len(params_list)

    def execute_and_commit(self, query: str, params=None) -> int:
        norm = " ".join(query.lower().split())
        params = params or ()

        if "delete from knowledge_chunks where doc_name = %s" in norm:
            doc_name = params[0]
            before = len(self.rows)
            self.rows = [row for row in self.rows if row["doc_name"] != doc_name]
            return before - len(self.rows)

        if "delete from knowledge_chunks where doc_id = %s" in norm:
            doc_id = params[0]
            before = len(self.rows)
            self.rows = [row for row in self.rows if row["doc_id"] != doc_id]
            return before - len(self.rows)

        if "delete from knowledge_chunks" in norm:
            before = len(self.rows)
            self.rows = []
            return before

        raise AssertionError(f"Unsupported execute_and_commit query: {query}")


class _FakeEmbeddingClient:
    def __init__(self, *args, embedding_dim=16, **kwargs):
        _ = args, kwargs
        self.embedding_dim = embedding_dim

    def _encode(self, text: str) -> List[float]:
        vec = [0.0] * self.embedding_dim
        for token in text.lower().split():
            token_hash = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            vec[token_hash % self.embedding_dim] += 1.0
        return vec

    def embed_text(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        _ = task_type
        return self._encode(text)

    def embed_batch(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        _ = task_type
        return [self._encode(text) for text in texts]


class TestDocumentProcessor:
    def test_load_pdf_with_mock_reader(self, monkeypatch, tmp_path: Path):
        class _FakePage:
            def __init__(self, text: str):
                self._text = text

            def extract_text(self):
                return self._text

        class _FakeReader:
            def __init__(self, _path: str):
                self.pages = [_FakePage("หน้าแรก"), _FakePage("\n"), _FakePage("หน้าที่สอง")]

        monkeypatch.setattr("pypdf.PdfReader", _FakeReader)

        fake_pdf = tmp_path / "sample.pdf"
        fake_pdf.write_text("placeholder", encoding="utf-8")

        text = load_pdf(fake_pdf)
        assert "หน้าแรก" in text
        assert "หน้าที่สอง" in text

    def test_load_docx_with_mock_document(self, monkeypatch, tmp_path: Path):
        class _Paragraph:
            def __init__(self, text: str):
                self.text = text

        class _FakeDocument:
            def __init__(self, _path: str):
                self.paragraphs = [_Paragraph("ย่อหน้า A"), _Paragraph("  "), _Paragraph("ย่อหน้า B")]

        monkeypatch.setattr("docx.Document", _FakeDocument)

        fake_docx = tmp_path / "sample.docx"
        fake_docx.write_text("placeholder", encoding="utf-8")

        text = load_docx(fake_docx)
        assert "ย่อหน้า A" in text
        assert "ย่อหน้า B" in text

    def test_chunk_text_and_chunk_document(self, tmp_path: Path):
        body = "\n\n".join([
            "ย่อหน้า 1 เกี่ยวกับแรงจูงใจ",
            "ย่อหน้า 2 เกี่ยวกับการรับมือความอยาก",
            "ย่อหน้า 3 เกี่ยวกับแผนป้องกันการกลับไปใช้ซ้ำ",
        ])
        chunks = chunk_text(body, chunk_size=60, overlap=10)
        assert len(chunks) >= 2
        assert all(chunk.strip() for chunk in chunks)

        doc_path = tmp_path / "guide.md"
        doc_path.write_text(body, encoding="utf-8")
        doc_chunks = chunk_document(str(doc_path), chunk_size=60, overlap=10)
        assert doc_chunks
        assert doc_chunks[0].metadata["source"] == "guide.md"

    def test_chunk_document_adds_language_and_token_metadata(self, tmp_path: Path):
        doc_path = tmp_path / "bilingual.md"
        doc_path.write_text("support plan relapse prevention\n\nแผนดูแลและป้องกันการกลับไปใช้ซ้ำ", encoding="utf-8")

        doc_chunks = chunk_document(str(doc_path), chunk_size=100, overlap=20)
        assert doc_chunks
        first_meta = doc_chunks[0].metadata
        assert first_meta.get("language") in {"en", "th", "mixed", "unknown"}
        assert int(first_meta.get("token_count") or 0) > 0
        assert int(first_meta.get("char_count") or 0) > 0

    def test_chunk_document_merges_short_chunks_in_token_mode(self, tmp_path: Path):
        doc_path = tmp_path / "mi-guide.md"
        doc_path.write_text(
            "\n\n".join(
                [
                    "## Session A\n" + ("change talk momentum " * 70).strip(),
                    "## Quick Check\nbrief follow up",
                    "## Session B\n" + ("planning and commitment language " * 70).strip(),
                ]
            ),
            encoding="utf-8",
        )

        doc_chunks = chunk_document(str(doc_path), chunk_size=1500, overlap=200)
        token_counts = [int(chunk.metadata.get("token_count") or 0) for chunk in doc_chunks]

        assert len(doc_chunks) == 2
        assert all(token_count >= 24 for token_count in token_counts)


class TestEmbeddingClient:
    def test_openrouter_default_embedding_model(self):
        client = EmbeddingClient(
            redis_client=None,
            openrouter_api_key="openrouter-test",
            gemini_api_key="",
            openai_api_key="",
            embedding_dim=4,
        )

        assert client.openrouter_model == "openai/text-embedding-3-small"

    def test_embed_text_prefers_openrouter(self, monkeypatch):
        redis_client = _DummyRedis()
        client = EmbeddingClient(
            redis_client=redis_client,
            openrouter_api_key="openrouter-test",
            gemini_api_key="gemini-test",
            openai_api_key="openai-test",
            embedding_dim=4,
        )

        calls = {"openrouter": 0}

        def _fake_openrouter(text):
            _ = text
            calls["openrouter"] += 1
            return [1.0, 0.0, 0.0, 0.0]

        monkeypatch.setattr(client, "_embed_text_openrouter", _fake_openrouter)
        monkeypatch.setattr(
            client,
            "_embed_text_gemini",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call gemini")),
        )
        monkeypatch.setattr(
            client,
            "_embed_text_openai",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call openai")),
        )

        first = client.embed_text("sample text")
        second = client.embed_text("sample text")

        assert first == [1.0, 0.0, 0.0, 0.0]
        assert second == first
        assert calls["openrouter"] == 1

    def test_embed_text_fallback_from_openrouter_to_gemini(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            openrouter_api_key="openrouter-test",
            gemini_api_key="gemini-test",
            openai_api_key="openai-test",
            embedding_dim=4,
        )

        call_order = []

        def _openrouter_fail(_text):
            call_order.append("openrouter")
            raise RuntimeError("openrouter down")

        def _gemini_ok(_text, task_type="RETRIEVAL_DOCUMENT"):
            _ = task_type
            call_order.append("gemini")
            return [0.0, 1.0, 0.0, 0.0]

        monkeypatch.setattr(client, "_embed_text_openrouter", _openrouter_fail)
        monkeypatch.setattr(client, "_embed_text_gemini", _gemini_ok)
        monkeypatch.setattr(
            client,
            "_embed_text_openai",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call openai")),
        )

        fallback = client.embed_text("different text")
        assert fallback == [0.0, 1.0, 0.0, 0.0]
        assert call_order == ["openrouter", "gemini"]

    def test_cache_and_fallback(self, monkeypatch):
        redis_client = _DummyRedis()
        client = EmbeddingClient(
            redis_client=redis_client,
            gemini_api_key="gemini-test",
            openai_api_key="openai-test",
            embedding_dim=4,
        )

        monkeypatch.setattr(client, "_embed_text_gemini", lambda text, task_type="RETRIEVAL_DOCUMENT": [1.0, 0.0, 0.0, 0.0])
        first = client.embed_text("sample text")
        assert first == [1.0, 0.0, 0.0, 0.0]

        # cache hit should bypass provider calls
        monkeypatch.setattr(client, "_embed_text_gemini", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not call")))
        second = client.embed_text("sample text")
        assert second == first

        monkeypatch.setattr(client, "_embed_text_gemini", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("gemini failed")))
        monkeypatch.setattr(client, "_embed_text_openai", lambda text: [0.0, 1.0, 0.0, 0.0])
        fallback = client.embed_text("different text")
        assert fallback == [0.0, 1.0, 0.0, 0.0]

    def test_embed_batch_prefers_openrouter_batch_and_caches(self, monkeypatch):
        redis_client = _DummyRedis()
        client = EmbeddingClient(
            redis_client=redis_client,
            openrouter_api_key="openrouter-test",
            gemini_api_key="gemini-test",
            openai_api_key="openai-test",
            embedding_dim=4,
        )

        calls = {"count": 0}

        def _fake_batch(texts):
            calls["count"] += 1
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(client, "_embed_batch_openrouter", _fake_batch)
        monkeypatch.setattr(
            client,
            "_embed_batch_gemini",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call gemini batch")),
        )
        monkeypatch.setattr(
            client,
            "_embed_batch_openai",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call openai batch")),
        )

        first = client.embed_batch(["a", "b"], task_type="RETRIEVAL_DOCUMENT")
        second = client.embed_batch(["a", "b"], task_type="RETRIEVAL_DOCUMENT")

        assert first == second == [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]
        assert calls["count"] == 1

    def test_embed_batch_fallback_from_openrouter_to_gemini(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            openrouter_api_key="openrouter-test",
            gemini_api_key="gemini-test",
            openai_api_key="openai-test",
            embedding_dim=4,
        )

        call_order = []

        def _openrouter_fail(_texts):
            call_order.append("openrouter")
            raise RuntimeError("openrouter down")

        def _gemini_ok(texts, task_type="RETRIEVAL_DOCUMENT"):
            _ = task_type
            call_order.append("gemini")
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(client, "_embed_batch_openrouter", _openrouter_fail)
        monkeypatch.setattr(client, "_embed_batch_gemini", _gemini_ok)
        monkeypatch.setattr(
            client,
            "_embed_batch_openai",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("should not call openai batch")),
        )

        emb = client.embed_batch(["a", "b"])
        assert emb == [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]
        assert call_order == ["openrouter", "gemini"]

    def test_embed_batch_prefers_gemini_batch_and_caches(self, monkeypatch):
        redis_client = _DummyRedis()
        client = EmbeddingClient(
            redis_client=redis_client,
            gemini_api_key="gemini-test",
            openai_api_key="",
            embedding_dim=4,
        )

        calls = {"count": 0}

        def _fake_batch(texts, task_type="RETRIEVAL_DOCUMENT"):
            _ = task_type
            calls["count"] += 1
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(client, "_embed_batch_gemini", _fake_batch)

        first = client.embed_batch(["a", "b"], task_type="RETRIEVAL_DOCUMENT")
        second = client.embed_batch(["a", "b"], task_type="RETRIEVAL_DOCUMENT")

        assert first == second == [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]
        assert calls["count"] == 1

    def test_embed_text_gemini_retries_on_429(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="gemini-test",
            openai_api_key="",
            embedding_dim=4,
            max_retries=3,
        )

        class _Resp:
            def __init__(self, status_code: int, body: dict):
                self.status_code = status_code
                self._body = body

            def raise_for_status(self):
                if self.status_code >= 400 and self.status_code != 429:
                    raise RuntimeError("http error")

            def json(self):
                return self._body

        responses = [
            _Resp(429, {}),
            _Resp(200, {"embedding": {"values": [1.0, 0.0, 0.0, 0.0]}}),
        ]

        monkeypatch.setattr("requests.post", lambda *args, **kwargs: responses.pop(0))
        monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

        emb = client.embed_text("retry test")
        assert emb == [1.0, 0.0, 0.0, 0.0]

    def test_embed_text_gemini_falls_back_to_next_model_on_404(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="gemini-test",
            openai_api_key="",
            gemini_model="bad-model-name",
            embedding_dim=4,
        )

        attempted_urls = []

        def _fake_post(url, payload):
            _ = payload
            attempted_urls.append(url)
            if "bad-model-name" in url:
                raise GeminiAPIError(404, "model not found")
            return {"embedding": {"values": [1.0, 2.0, 3.0, 4.0]}}

        monkeypatch.setattr(client, "_post_gemini_with_retry", _fake_post)

        emb = client._embed_text_gemini("hello")
        assert emb == [1.0, 2.0, 3.0, 4.0]
        assert any("bad-model-name" in url for url in attempted_urls)
        assert client.gemini_model != "bad-model-name"

    def test_post_gemini_error_does_not_include_api_key(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="secret-test-key",
            openai_api_key="",
            embedding_dim=4,
            max_retries=1,
        )

        class _Resp:
            status_code = 404

            def json(self):
                return {"error": {"message": "model not found"}}

        monkeypatch.setattr("requests.post", lambda *args, **kwargs: _Resp())

        try:
            client._post_gemini_with_retry(
                "https://generativelanguage.googleapis.com/v1beta/models/bad:embedContent?key=secret-test-key",
                {"model": "models/bad"},
            )
            assert False, "Expected GeminiAPIError"
        except GeminiAPIError as exc:
            assert "secret-test-key" not in str(exc)

    def test_embed_text_gemini_requests_output_dimensionality(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="gemini-test",
            openai_api_key="",
            embedding_dim=8,
        )

        captured_payload = {}

        def _fake_post(_url, payload):
            captured_payload["payload"] = payload
            return {"embedding": {"values": [0.1] * 8}}

        monkeypatch.setattr(client, "_post_gemini_with_retry", _fake_post)

        emb = client._embed_text_gemini("hello")
        assert emb == [0.1] * 8
        assert captured_payload["payload"].get("outputDimensionality") == 8

    def test_embed_text_gemini_retries_without_output_dimensionality_when_unsupported(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="gemini-test",
            openai_api_key="",
            embedding_dim=4,
        )

        attempted_payloads = []

        def _fake_post(_url, payload):
            attempted_payloads.append(payload)
            if "outputDimensionality" in payload:
                raise GeminiAPIError(400, 'Unknown name "outputDimensionality"')
            return {"embedding": {"values": [1.0, 2.0, 3.0, 4.0]}}

        monkeypatch.setattr(client, "_post_gemini_with_retry", _fake_post)

        emb = client._embed_text_gemini("hello")
        assert emb == [1.0, 2.0, 3.0, 4.0]
        assert len(attempted_payloads) >= 2
        assert "outputDimensionality" in attempted_payloads[0]
        assert "outputDimensionality" not in attempted_payloads[1]

    def test_embed_batch_gemini_retries_without_output_dimensionality_when_unsupported(self, monkeypatch):
        client = EmbeddingClient(
            redis_client=None,
            gemini_api_key="gemini-test",
            openai_api_key="",
            embedding_dim=4,
        )

        attempted_payloads = []

        def _fake_post(_url, payload):
            attempted_payloads.append(payload)
            first_request = (payload.get("requests") or [{}])[0]
            if "outputDimensionality" in first_request:
                raise GeminiAPIError(400, "outputDimensionality unsupported")
            return {
                "embeddings": [
                    {"values": [1.0, 0.0, 0.0, 0.0]},
                    {"values": [0.0, 1.0, 0.0, 0.0]},
                ]
            }

        monkeypatch.setattr(client, "_post_gemini_with_retry", _fake_post)

        emb = client._embed_batch_gemini(["a", "b"])
        assert emb == [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
        assert len(attempted_payloads) >= 2
        assert "outputDimensionality" in attempted_payloads[0]["requests"][0]
        assert "outputDimensionality" not in attempted_payloads[1]["requests"][0]


class TestVectorStore:
    def test_add_search_delete_and_clear(self):
        db = _InMemoryDBManager()
        store = VectorStore(db_manager=db, embedding_dim=4)

        chunks = [
            DocumentChunk("alpha support", {"doc_id": "d1", "doc_name": "a.md", "chunk_index": 0}),
            DocumentChunk("beta relapse", {"doc_id": "d2", "doc_name": "b.md", "chunk_index": 0}),
        ]
        embeddings = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]

        inserted = store.add_chunks(chunks, embeddings)
        assert inserted == 2
        assert store.get_stats()["total_chunks"] == 2

        results = store.search([1.0, 0.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0].doc_id == "d1"

        assert store.delete_document("d1") is True
        assert store.get_stats()["total_chunks"] == 1

        cleared = store.clear()
        assert cleared == 1
        assert store.get_stats()["total_chunks"] == 0

    def test_search_lexical_returns_expected_document(self):
        db = _InMemoryDBManager()
        store = VectorStore(db_manager=db, embedding_dim=4)

        chunks = [
            DocumentChunk("relapse prevention coping plan", {"doc_id": "d1", "doc_name": "relapse.md", "chunk_index": 0}),
            DocumentChunk("sleep hygiene and nutrition", {"doc_id": "d2", "doc_name": "wellness.md", "chunk_index": 0}),
        ]
        embeddings = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]

        store.add_chunks(chunks, embeddings)
        results = store.search_lexical("need relapse prevention coping", top_k=2)
        assert results
        assert results[0].doc_name == "relapse.md"

    def test_add_chunks_uses_hex_embedding_payload_for_db_safety(self):
        class _CaptureDB(_InMemoryDBManager):
            def __init__(self):
                super().__init__()
                self.last_params_list: List[tuple] = []

            def execute_many(self, query: str, params_list: List[tuple]) -> int:
                self.last_params_list = params_list
                return super().execute_many(query, params_list)

        db = _CaptureDB()
        store = VectorStore(db_manager=db, embedding_dim=4)

        inserted = store.add_chunks(
            [DocumentChunk("gamma", {"doc_id": "d3", "doc_name": "c.md", "chunk_index": 0})],
            [[0.1, 0.2, 0.3, 0.4]],
        )

        assert inserted == 1
        assert db.last_params_list
        embedding_param = db.last_params_list[0][4]
        assert isinstance(embedding_param, str)
        assert embedding_param
        assert all(ch in "0123456789abcdef" for ch in embedding_param)


class TestKnowledgeBase:
    def test_ingest_directory_query_and_reindex(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_a = tmp_path / "a.md"
        doc_b = tmp_path / "b.md"
        doc_a.write_text("relapse prevention coping plan support network", encoding="utf-8")
        doc_b.write_text("sleep nutrition hydration exercise", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )

        ingest_result = kb.ingest_directory()
        assert ingest_result["indexed"] == 2
        assert kb.get_stats()["total_documents"] == 2

        context = kb.query("ต้องการแผน coping ป้องกัน relapse", top_k=2)
        assert "a.md" in context

        skipped = kb.ingest_file(str(doc_a))
        assert skipped["status"] == "skipped"

        reindexed = kb.ingest_file(str(doc_a), force_reindex=True)
        assert reindexed["status"] == "indexed"

    def test_ingest_replaces_old_chunks_when_same_filename_changes(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_path = tmp_path / "policy.md"
        doc_path.write_text("version one relapse prevention", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )

        first = kb.ingest_file(str(doc_path))
        assert first["status"] == "indexed"
        first_doc_id = first["doc_id"]

        doc_path.write_text("version two crisis referral workflow", encoding="utf-8")
        second = kb.ingest_file(str(doc_path))
        assert second["status"] == "indexed"
        assert second["doc_id"] != first_doc_id

        doc_ids = db.execute_query(
            "SELECT DISTINCT doc_id FROM knowledge_chunks WHERE doc_name = %s",
            (doc_path.name,),
        )
        assert len(doc_ids) == 1
        assert doc_ids[0][0] == second["doc_id"]

    def test_query_returns_empty_when_embedding_fails(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_path = tmp_path / "support.md"
        doc_path.write_text("support coping plan", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )
        kb.ingest_file(str(doc_path))

        monkeypatch.setattr(
            kb.embedding_client,
            "embed_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding down")),
        )

        assert kb.query("ต้องทำยังไงต่อ") == ""

    def test_query_uses_lexical_fallback_when_dense_fails(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_path = tmp_path / "relapse_guide.md"
        doc_path.write_text("relapse prevention coping plan trigger map", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )
        kb.ingest_file(str(doc_path))

        monkeypatch.setattr(
            kb.embedding_client,
            "embed_text",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("embedding down")),
        )

        context = kb.query("need relapse prevention plan", top_k=2)
        assert "relapse_guide.md" in context

    def test_query_with_sources_returns_source_metadata(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_path = tmp_path / "guide.md"
        doc_path.write_text("coping plan support prevention", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )
        kb.ingest_file(str(doc_path))

        result = kb.query_with_sources("ช่วยทำแผน coping", top_k=3)

        assert result["context"]
        assert result["sources"]
        assert result["sources"][0]["doc_name"] == "guide.md"

    def test_preprocess_query_adds_cross_lingual_hints_for_thai_relapse(self):
        processed = KnowledgeBase._preprocess_query(
            "\u0e01\u0e25\u0e31\u0e27\u0e08\u0e30\u0e01\u0e25\u0e31\u0e1a\u0e44\u0e1b\u0e43\u0e0a\u0e49\u0e2d\u0e35\u0e01"
        )
        assert "relapse" in processed
        assert "coping plan" in processed

    def test_query_can_retrieve_english_doc_from_thai_relapse_query(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        doc_path = tmp_path / "relapse_guide.md"
        doc_path.write_text("relapse prevention coping plan trigger map", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=80,
            overlap=10,
            embedding_dim=16,
        )
        kb.ingest_file(str(doc_path))

        context = kb.query(
            "\u0e01\u0e25\u0e31\u0e27\u0e08\u0e30\u0e01\u0e25\u0e31\u0e1a\u0e44\u0e1b\u0e43\u0e0a\u0e49 \u0e2d\u0e22\u0e32\u0e01\u0e44\u0e14\u0e49\u0e41\u0e1c\u0e19\u0e1b\u0e49\u0e2d\u0e07\u0e01\u0e31\u0e19",
            top_k=2,
        )
        assert "relapse_guide.md" in context

    def test_ingest_sets_topic_for_new_filename_patterns(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.rag.knowledge_base.EmbeddingClient", _FakeEmbeddingClient)

        db = _InMemoryDBManager()
        miti_doc = tmp_path / "miti4_2.md"
        research_doc = tmp_path / "Dtsch_Arztebl_Int-118_0109.md"
        miti_doc.write_text("change talk coding manual examples", encoding="utf-8")
        research_doc.write_text("evidence based motivational interviewing outcomes", encoding="utf-8")

        kb = KnowledgeBase(
            db_manager=db,
            redis_client=None,
            docs_dir=str(tmp_path),
            chunk_size=120,
            overlap=20,
            embedding_dim=16,
        )

        kb.ingest_file(str(miti_doc))
        kb.ingest_file(str(research_doc))

        topics = {
            str(record.get("doc_name")): str((record.get("metadata") or {}).get("topic") or "")
            for record in kb.vector_store._records
        }
        assert topics.get("miti4_2.md") == "mi_quality"
        assert topics.get("Dtsch_Arztebl_Int-118_0109.md") == "research"


class TestIntegration:
    def test_generate_ai_response_injects_rag_context(self, monkeypatch):
        from app.database_manager import DatabaseManager

        # Prevent 60s socket wait in DatabaseManager during app_main import.
        monkeypatch.setattr(DatabaseManager, "_wait_for_database", lambda self, max_wait_time=60, retry_interval=2: None)
        app_main = importlib.import_module("app.app_main")

        captured = {}

        def fake_send_chat(*, messages, model=None, **kwargs):
            _ = model, kwargs
            captured["messages"] = messages
            return "ok-response"

        class _Counter:
            def count_message_tokens(self, messages):
                _ = messages
                return 120

            def count_tokens(self, text):
                _ = text
                return 20

        monkeypatch.setattr(app_main.config, "MULTI_AI_ENABLED", False, raising=False)
        monkeypatch.setattr(app_main, "token_counter", _Counter())
        monkeypatch.setattr(app_main, "track_grok_call", lambda **kwargs: None)
        monkeypatch.setattr(app_main._xai_circuit_breaker, "call", lambda func, **kwargs: func(**kwargs))
        monkeypatch.setattr(app_main.grok_client, "send_chat", fake_send_chat)

        response = app_main.generate_ai_response_with_timeout(
            [{"role": "user", "content": "ช่วยผมวางแผนหน่อย"}],
            timeout=10,
            user_id="u-test",
            rag_context="[1] แหล่งข้อมูล: guide.md\nควรตั้งเป้าหมายเล็กๆ",
        )

        assert response == "ok-response"
        sent_messages = captured["messages"]
        assert any(
            msg.get("role") == "system" and "บริบทความรู้จากระบบ" in msg.get("content", "")
            for msg in sent_messages
        )

    def test_generate_ai_response_without_rag_context_does_not_inject(self, monkeypatch):
        from app.database_manager import DatabaseManager

        monkeypatch.setattr(DatabaseManager, "_wait_for_database", lambda self, max_wait_time=60, retry_interval=2: None)
        app_main = importlib.import_module("app.app_main")

        captured = {}

        def fake_send_chat(*, messages, model=None, **kwargs):
            _ = model, kwargs
            captured["messages"] = messages
            return "ok-response"

        class _Counter:
            def count_message_tokens(self, messages):
                _ = messages
                return 80

            def count_tokens(self, text):
                _ = text
                return 10

        monkeypatch.setattr(app_main.config, "MULTI_AI_ENABLED", False, raising=False)
        monkeypatch.setattr(app_main, "token_counter", _Counter())
        monkeypatch.setattr(app_main, "track_grok_call", lambda **kwargs: None)
        monkeypatch.setattr(app_main._xai_circuit_breaker, "call", lambda func, **kwargs: func(**kwargs))
        monkeypatch.setattr(app_main.grok_client, "send_chat", fake_send_chat)

        app_main.generate_ai_response_with_timeout(
            [{"role": "user", "content": "สวัสดี"}],
            timeout=10,
            user_id="u-test",
            rag_context=None,
        )

        assert not any(
            msg.get("content", "").startswith("บริบทความรู้จากระบบ (สำหรับ AI เท่านั้น):")
            for msg in captured["messages"]
            if msg.get("role") == "system"
        )

    def test_generate_ai_response_injects_info_grounding_without_rag(self, monkeypatch):
        from app.database_manager import DatabaseManager

        monkeypatch.setattr(DatabaseManager, "_wait_for_database", lambda self, max_wait_time=60, retry_interval=2: None)
        app_main = importlib.import_module("app.app_main")

        captured = {}

        def fake_send_chat(*, messages, model=None, **kwargs):
            _ = model, kwargs
            captured["messages"] = messages
            return "ok-response"

        class _Counter:
            def count_message_tokens(self, messages):
                _ = messages
                return 90

            def count_tokens(self, text):
                _ = text
                return 15

        monkeypatch.setattr(app_main.config, "MULTI_AI_ENABLED", False, raising=False)
        monkeypatch.setattr(app_main, "token_counter", _Counter())
        monkeypatch.setattr(app_main, "track_grok_call", lambda **kwargs: None)
        monkeypatch.setattr(app_main._xai_circuit_breaker, "call", lambda func, **kwargs: func(**kwargs))
        monkeypatch.setattr(app_main.grok_client, "send_chat", fake_send_chat)

        app_main.generate_ai_response_with_timeout(
            [{"role": "user", "content": "ยาบ้าคืออะไร"}],
            timeout=10,
            user_id="u-test",
            rag_context=None,
        )

        assert any(
            msg.get("role") == "system" and "คำแนะนำเพิ่มเติมสำหรับคำถามเชิงข้อมูล" in msg.get("content", "")
            for msg in captured["messages"]
        )
        assert any(
            msg.get("role") == "system" and "อย่าคาดเดา" in msg.get("content", "")
            for msg in captured["messages"]
        )


class TestKnowledgeBaseInit:
    def test_init_knowledge_base_disabled_returns_none(self):
        kb = init_knowledge_base(db_manager=object(), enabled=False)
        assert kb is None
        assert get_knowledge_base() is None

    def test_init_knowledge_base_handles_constructor_failure(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.knowledge_base.KnowledgeBase",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("init failed")),
        )
        kb = init_knowledge_base(db_manager=object(), enabled=True)
        assert kb is None
