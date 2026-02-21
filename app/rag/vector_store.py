"""MySQL-backed vector store with in-memory dense and lexical retrieval."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from .document_processor import DocumentChunk


@dataclass
class SearchResult:
    doc_id: str
    doc_name: str
    chunk_index: int
    content: str
    score: float
    metadata: Dict[str, object]


class VectorStore:
    """Persist chunk embeddings in MySQL and serve in-memory hybrid retrieval."""

    def __init__(self, db_manager, embedding_dim: int = 1536):
        self.db_manager = db_manager
        self.embedding_dim = max(1, int(embedding_dim))

        self._records: List[Dict[str, object]] = []
        self._matrix = np.empty((0, self.embedding_dim), dtype=np.float32)
        self._norms = np.empty((0,), dtype=np.float32)

        self._token_pattern = re.compile(r"[A-Za-z0-9_]{2,}|[\u0E00-\u0E7F]+")
        self._term_postings: Dict[str, List[Tuple[int, int]]] = {}
        self._idf: Dict[str, float] = {}
        self._doc_lengths = np.empty((0,), dtype=np.float32)
        self._avg_doc_length = 0.0
        self._bm25_k1 = 1.5
        self._bm25_b = 0.75

        self.reload()

    def _coerce_embedding(self, embedding: List[float]) -> np.ndarray:
        vec = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vec.size == self.embedding_dim:
            return vec
        if vec.size > self.embedding_dim:
            return vec[: self.embedding_dim]
        padded = np.zeros(self.embedding_dim, dtype=np.float32)
        padded[: vec.size] = vec
        return padded

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        return self._coerce_embedding(embedding).astype(np.float32).tobytes()

    def _serialize_embedding_hex(self, embedding: List[float]) -> str:
        return self._serialize_embedding(embedding).hex()

    def _deserialize_embedding(self, blob: bytes) -> np.ndarray:
        if not blob:
            return np.zeros(self.embedding_dim, dtype=np.float32)

        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.size == self.embedding_dim:
            return vec

        if len(blob) % 8 == 0:
            vec64 = np.frombuffer(blob, dtype=np.float64)
            if vec64.size > 0:
                return self._coerce_embedding(vec64.tolist())

        return self._coerce_embedding(vec.tolist())

    def _tokenize_for_lexical(self, text: str) -> List[str]:
        base_tokens = self._token_pattern.findall((text or "").lower())
        if not base_tokens:
            return []

        tokens: List[str] = []
        for token in base_tokens:
            if re.search(r"[\u0E00-\u0E7F]", token) and len(token) > 8:
                tokens.append(token)
                for index in range(0, len(token) - 2):
                    tokens.append(token[index : index + 3])
                continue
            tokens.append(token)
        return tokens

    @staticmethod
    def _resolve_metadata(metadata_raw) -> Dict[str, object]:
        if isinstance(metadata_raw, dict):
            return metadata_raw
        if isinstance(metadata_raw, (bytes, bytearray)) and metadata_raw:
            try:
                return json.loads(metadata_raw.decode("utf-8", errors="ignore"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
        if isinstance(metadata_raw, str) and metadata_raw.strip():
            try:
                return json.loads(metadata_raw)
            except json.JSONDecodeError:
                return {}
        return {}

    def _append_runtime_rows(self, runtime_rows: List[Dict[str, object]], vectors: List[np.ndarray]) -> None:
        if not runtime_rows:
            return

        self._records.extend(runtime_rows)
        if vectors:
            matrix_new = np.vstack(vectors).astype(np.float32)
            if self._matrix.size == 0:
                self._matrix = matrix_new
            else:
                self._matrix = np.vstack([self._matrix, matrix_new]).astype(np.float32)

            norms_new = np.linalg.norm(matrix_new, axis=1)
            norms_new = np.where(norms_new == 0, 1e-12, norms_new)
            if self._norms.size == 0:
                self._norms = norms_new
            else:
                self._norms = np.concatenate([self._norms, norms_new]).astype(np.float32)

        self._rebuild_lexical_index()

    def _rebuild_lexical_index(self) -> None:
        doc_count = len(self._records)
        if doc_count == 0:
            self._term_postings = {}
            self._idf = {}
            self._doc_lengths = np.empty((0,), dtype=np.float32)
            self._avg_doc_length = 0.0
            return

        postings: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        doc_frequency: Counter = Counter()
        doc_lengths = np.zeros((doc_count,), dtype=np.float32)

        for doc_index, record in enumerate(self._records):
            tokens = self._tokenize_for_lexical(str(record.get("content") or ""))
            if not tokens:
                continue
            token_counts = Counter(tokens)
            doc_lengths[doc_index] = float(sum(token_counts.values()))
            for token, freq in token_counts.items():
                postings[token].append((doc_index, int(freq)))
            doc_frequency.update(token_counts.keys())

        avg_doc_len = float(np.mean(doc_lengths)) if doc_count > 0 else 0.0
        if avg_doc_len <= 0.0:
            avg_doc_len = 1.0

        idf: Dict[str, float] = {}
        for token, freq in doc_frequency.items():
            # BM25 Robertson/Sparck Jones idf
            numerator = doc_count - freq + 0.5
            denominator = freq + 0.5
            idf[token] = math.log(1.0 + (numerator / max(denominator, 1e-12)))

        self._term_postings = dict(postings)
        self._idf = idf
        self._doc_lengths = doc_lengths
        self._avg_doc_length = avg_doc_len

    @staticmethod
    def _top_indices(scores: np.ndarray, top_k: int) -> np.ndarray:
        if scores.size == 0:
            return np.empty((0,), dtype=np.int64)
        top_k = max(1, min(int(top_k), int(scores.size)))
        if top_k >= scores.size:
            return np.argsort(scores)[::-1]
        candidate_indices = np.argpartition(scores, -top_k)[-top_k:]
        sorted_local = np.argsort(scores[candidate_indices])[::-1]
        return candidate_indices[sorted_local]

    def _build_results_from_scores(self, scores: np.ndarray, indices: np.ndarray) -> List[SearchResult]:
        results: List[SearchResult] = []
        for raw_index in indices:
            index = int(raw_index)
            if index < 0 or index >= len(self._records):
                continue
            score = float(scores[index])
            if not np.isfinite(score):
                continue
            record = self._records[index]
            results.append(
                SearchResult(
                    doc_id=str(record.get("doc_id") or ""),
                    doc_name=str(record.get("doc_name") or ""),
                    chunk_index=int(record.get("chunk_index") or 0),
                    content=str(record.get("content") or ""),
                    score=score,
                    metadata=dict(record.get("metadata") or {}),
                )
            )
        return results

    def add_chunks(self, chunks: List[DocumentChunk], embeddings: List[List[float]]) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        if not chunks:
            return 0

        db_rows = []
        runtime_rows: List[Dict[str, object]] = []
        runtime_vectors: List[np.ndarray] = []

        for chunk, embedding in zip(chunks, embeddings):
            metadata = dict(chunk.metadata or {})
            doc_id = str(metadata.get("doc_id") or "")
            doc_name = str(metadata.get("doc_name") or metadata.get("source") or "unknown")
            chunk_index = int(metadata.get("chunk_index", 0))
            content = str(chunk.content or "")

            db_rows.append(
                (
                    doc_id,
                    doc_name,
                    chunk_index,
                    content,
                    self._serialize_embedding_hex(embedding),
                    json.dumps(metadata, ensure_ascii=False),
                )
            )
            runtime_rows.append(
                {
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "chunk_index": chunk_index,
                    "content": content,
                    "metadata": metadata,
                }
            )
            runtime_vectors.append(self._coerce_embedding(embedding))

        query = """
            INSERT INTO knowledge_chunks
            (doc_id, doc_name, chunk_index, content, embedding, metadata)
            VALUES (%s, %s, %s, %s, UNHEX(%s), %s)
        """
        self.db_manager.execute_many(query, db_rows)
        self._append_runtime_rows(runtime_rows, runtime_vectors)
        logging.info("RAG ingest persisted %s chunks into knowledge_chunks", len(db_rows))
        return len(db_rows)

    def search_dense(self, query_embedding: List[float], top_k: int = 3) -> List[SearchResult]:
        if self._matrix.size == 0:
            return []

        query_vec = self._coerce_embedding(query_embedding)
        query_norm = float(np.linalg.norm(query_vec))
        if query_norm == 0.0:
            return []

        scores = (self._matrix @ query_vec) / (self._norms * query_norm)
        best_indices = self._top_indices(scores, top_k=top_k)
        return self._build_results_from_scores(scores, best_indices)

    def search(self, query_embedding: List[float], top_k: int = 3) -> List[SearchResult]:
        # Backward-compatible alias for previous dense-only behavior.
        return self.search_dense(query_embedding=query_embedding, top_k=top_k)

    def search_lexical(self, query_text: str, top_k: int = 8) -> List[SearchResult]:
        if not self._records:
            return []

        query_tokens = self._tokenize_for_lexical(query_text)
        if not query_tokens:
            return []

        query_counts = Counter(query_tokens)
        scores = np.zeros((len(self._records),), dtype=np.float32)
        avg_len = max(self._avg_doc_length, 1e-6)

        for token, query_freq in query_counts.items():
            postings = self._term_postings.get(token)
            if not postings:
                continue
            idf = self._idf.get(token, 0.0)
            if idf <= 0.0:
                continue
            query_weight = 1.0 + math.log1p(float(query_freq))

            for doc_index, term_freq in postings:
                doc_len = float(self._doc_lengths[doc_index]) if doc_index < self._doc_lengths.size else 0.0
                norm = term_freq + self._bm25_k1 * (1.0 - self._bm25_b + self._bm25_b * (doc_len / avg_len))
                if norm <= 0:
                    continue
                scores[doc_index] += float(idf * ((term_freq * (self._bm25_k1 + 1.0)) / norm) * query_weight)

        if not np.any(scores > 0):
            return []

        best_indices = self._top_indices(scores, top_k=top_k)
        positive_indices = np.asarray([index for index in best_indices if scores[int(index)] > 0], dtype=np.int64)
        return self._build_results_from_scores(scores, positive_indices)

    def delete_document(self, doc_id: str) -> bool:
        affected = self.db_manager.execute_and_commit(
            "DELETE FROM knowledge_chunks WHERE doc_id = %s",
            (doc_id,),
        )
        self.reload()
        return affected > 0

    def delete_by_doc_name(self, doc_name: str) -> int:
        affected = self.db_manager.execute_and_commit(
            "DELETE FROM knowledge_chunks WHERE doc_name = %s",
            (doc_name,),
        )
        self.reload()
        return affected

    def clear(self) -> int:
        affected = self.db_manager.execute_and_commit("DELETE FROM knowledge_chunks")
        self.reload()
        return affected

    def reload(self) -> None:
        query = """
            SELECT id, doc_id, doc_name, chunk_index, content, embedding, metadata
            FROM knowledge_chunks
            ORDER BY id ASC
        """
        rows = self.db_manager.execute_query(query) or []

        records: List[Dict[str, object]] = []
        vectors: List[np.ndarray] = []

        for row in rows:
            _, doc_id, doc_name, chunk_index, content, embedding_blob, metadata_raw = row
            metadata = self._resolve_metadata(metadata_raw)

            records.append(
                {
                    "doc_id": doc_id,
                    "doc_name": doc_name,
                    "chunk_index": chunk_index,
                    "content": content,
                    "metadata": metadata,
                }
            )
            vectors.append(self._deserialize_embedding(embedding_blob))

        self._records = records
        if vectors:
            self._matrix = np.vstack(vectors).astype(np.float32)
            norms = np.linalg.norm(self._matrix, axis=1)
            self._norms = np.where(norms == 0, 1e-12, norms)
        else:
            self._matrix = np.empty((0, self.embedding_dim), dtype=np.float32)
            self._norms = np.empty((0,), dtype=np.float32)

        self._rebuild_lexical_index()

    def get_stats(self) -> Dict[str, object]:
        doc_ids = {str(record["doc_id"]) for record in self._records if record.get("doc_id")}
        return {
            "total_chunks": len(self._records),
            "total_documents": len(doc_ids),
            "embedding_dim": self.embedding_dim,
            "lexical_vocab_size": len(self._idf),
        }
