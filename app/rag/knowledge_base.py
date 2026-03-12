"""High-level knowledge base API for document ingestion and hybrid retrieval."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .document_processor import DocumentChunk, chunk_document
from .embedding_client import EmbeddingClient
from .vector_store import SearchResult, VectorStore

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}|[\u0E00-\u0E7F]+")
_FILLER_PATTERN = re.compile(
    r"\b(?:please|pls|kindly|help|ครับ|ค่ะ|คะ|นะ|หน่อย)\b",
    flags=re.IGNORECASE,
)
_LOW_SIGNAL_SECTION_TITLES = {
    "related resources",
    "resources",
    "references",
}

_TOPIC_EXPANSION_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (
        (
            "crisis",
            "emergency",
            "urgent",
            "suicide",
            "self-harm",
            "harm myself",
            "ฉุกเฉิน",
            "เร่งด่วน",
            "ทำร้ายตัวเอง",
            "ฆ่าตัวตาย",
            "ไม่อยากมีชีวิตอยู่",
        ),
        "emergency hotline safety plan urgent referral 1323 1669",
    ),
    (
        (
            "overdose",
            "naloxone",
            "narcan",
            "fentanyl",
            "opioid",
            "stimulant",
            "เสพเกินขนาด",
            "โอเวอร์โดส",
            "หมดสติ",
            "นาล็อกโซน",
            "เฟนทานิล",
        ),
        "opioid fentanyl naloxone overdose rescue breathing emergency referral hospital 1669",
    ),
    (
        (
            "relapse",
            "lapse",
            "craving",
            "urge",
            "trigger",
            "กลับไปใช้",
            "กลับไปเสพ",
            "ใช้ซ้ำ",
            "เสพซ้ำ",
            "เผลอใช้",
            "ความอยาก",
            "ตัวกระตุ้น",
        ),
        "relapse lapse craving urge trigger coping plan prevention recovery urge surfing",
    ),
    (
        (
            "withdrawal",
            "detox",
            "ลงแดง",
            "ถอนยา",
            "อาการถอน",
        ),
        "withdrawal symptoms monitoring medical support detox",
    ),
    (
        (
            "meth",
            "methamphetamine",
            "ice",
            "yaba",
            "shabu",
            "ยาบ้า",
            "เมท",
        ),
        "methamphetamine stimulant yaba shabu psychosis contingency management stimulant crash",
    ),
    (
        (
            "heroin",
            "เฮโรอีน",
            "black tar",
        ),
        "heroin opioid overdose naloxone withdrawal medication treatment methadone buprenorphine",
    ),
    (
        (
            "cannabis",
            "marijuana",
            "weed",
            "กัญชา",
        ),
        "cannabis marijuana weed thc cbd impairment psychosis motivational support",
    ),
    (
        (
            "opioids",
            "opiate",
            "oxycodone",
            "ยาแก้ปวด",
            "โอปิออยด์",
        ),
        "opioids opioid overdose naloxone buprenorphine methadone naltrexone withdrawal",
    ),
    (
        (
            "motivational interviewing",
            "change talk",
            "sustain talk",
            "ambivalence",
            "oars",
            "reflection",
            "confidence",
            "motivation",
            "readiness",
            "แรงจูงใจ",
            "ความพร้อมเปลี่ยน",
            "สัมภาษณ์เสริมแรงจูงใจ",
            "ลังเลสองใจ",
            "ความมั่นใจ",
        ),
        "motivational interviewing oars reflection ambivalence readiness confidence change talk sustain talk",
    ),
    (
        (
            "family",
            "caregiver",
            "ครอบครัว",
            "ญาติ",
            "ผู้ดูแล",
        ),
        "craft communication boundaries support plan family",
    ),
    (
        (
            "cbt",
            "ความคิดอัตโนมัติ",
            "บันทึกความคิด",
        ),
        "cognitive reframing thought record behavioural activation",
    ),
    (
        (
            "dbt",
            "กำกับอารมณ์",
            "ทนทานความเครียด",
        ),
        "distress tolerance emotion regulation mindfulness",
    ),
]

_DOC_TOPIC_MAP = {
    "miti": "mi_quality",
    "integrity": "mi_quality",
    "manual": "mi_quality",
    "arztebl": "research",
    "dtsch": "research",
    "evidence": "research",
    "motivational": "mi",
    "mi_core": "mi",
    "change_talk": "mi",
    "quality_checklist": "mi_quality",
    "evidence_summary": "research",
    "stages": "stages_of_change",
    "harm_reduction": "harm_reduction",
    "cbt": "cbt_dbt",
    "dbt": "cbt_dbt",
    "crisis": "crisis",
    "relapse": "relapse",
    "craving": "relapse",
    "overdose": "crisis",
    "withdrawal": "crisis",
    "family": "family",
    "conversation": "examples",
    "research": "research",
}
_SOURCE_PROFILE_RULES: List[Tuple[Tuple[str, ...], Tuple[str, float]]] = [
    (("overdose", "withdrawal"), ("crisis_reference", 1.12)),
    (("relapse", "craving"), ("relapse_reference", 1.10)),
    (("mi_core", "change_talk", "motivational"), ("mi_reference", 1.06)),
    (("quality_checklist", "miti"), ("mi_quality_reference", 0.97)),
    (("evidence_summary", "dtsch", "arztebl", "research"), ("evidence_review", 0.97)),
    (
        ("fentanyl", "heroin", "methamphetamine", "opioids", "treatment", "prevention", "hiv"),
        ("focused_reference", 1.03),
    ),
    (("emerging", "addiction_science", "psychedelic"), ("background_reference", 0.99)),
    (("manual", "drugs_a_to_z", "encyclopedia"), ("broad_reference", 0.86)),
]


def _infer_topic(doc_name: str) -> str:
    lower = str(doc_name or "").lower()
    for pattern, topic in _DOC_TOPIC_MAP.items():
        if pattern in lower:
            return topic
    return "general"


def _infer_source_profile(doc_name: str) -> Tuple[str, float]:
    lower = str(doc_name or "").lower()
    for patterns, profile in _SOURCE_PROFILE_RULES:
        if any(pattern in lower for pattern in patterns):
            return profile
    return ("general_reference", 1.0)


def _tokenize(text: str) -> List[str]:
    return _TOKEN_PATTERN.findall((text or "").lower())


class KnowledgeBase:
    """Ingest local docs and retrieve relevant context snippets."""

    def __init__(
        self,
        db_manager,
        redis_client=None,
        docs_dir: str = "knowledge_docs",
        chunk_size: int = 1100,
        overlap: int = 120,
        embedding_dim: int = 1536,
        min_score: float = 0.42,
        base_fetch_k: int = 36,
        max_context_chars: int = 5200,
        max_chunk_chars: int = 1800,
    ):
        self.db_manager = db_manager
        self.redis_client = redis_client
        self.docs_dir = str(Path(docs_dir))
        self.chunk_size = int(chunk_size)
        self.overlap = int(overlap)
        self.embedding_dim = max(1, int(embedding_dim))
        self.min_score = float(min_score)
        self.base_fetch_k = max(8, int(base_fetch_k))
        self.max_context_chars = max(1000, int(max_context_chars))
        self.max_chunk_chars = max(500, int(max_chunk_chars))
        self.lexical_min_score = 0.12

        self.dense_weight = 0.52
        self.lexical_weight = 0.30
        self.keyword_weight = 0.18
        self.max_chunks_per_doc = 2
        self.doc_reuse_penalty = 0.08

        self.embedding_client = EmbeddingClient(
            redis_client=redis_client,
            embedding_dim=self.embedding_dim,
        )
        self.vector_store = VectorStore(db_manager, embedding_dim=self.embedding_dim)

    def _compute_doc_id(self, path: Path) -> str:
        digest = hashlib.sha256()
        digest.update(str(path.resolve()).encode("utf-8"))
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()[:64]

    def _get_doc_ids_by_name(self, doc_name: str) -> List[str]:
        rows = self.db_manager.execute_query(
            "SELECT DISTINCT doc_id FROM knowledge_chunks WHERE doc_name = %s",
            (doc_name,),
        )
        return [str(row[0]) for row in rows if row and row[0]]

    @staticmethod
    def _is_path_within_directory(path_value: str, target_dir: Path) -> bool:
        try:
            return Path(path_value).resolve().is_relative_to(target_dir.resolve())
        except Exception:
            return False

    def _document_exists(self, doc_id: str) -> bool:
        rows = self.db_manager.execute_query(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE doc_id = %s",
            (doc_id,),
        )
        return bool(rows and rows[0][0] > 0)

    def ingest_file(self, file_path: str, force_reindex: bool = False) -> Dict[str, object]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Knowledge file not found: {file_path}")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported knowledge file type: {path.suffix}")

        doc_id = self._compute_doc_id(path)
        existing_doc_ids = set(self._get_doc_ids_by_name(path.name))
        if existing_doc_ids:
            if doc_id in existing_doc_ids and not force_reindex:
                return {
                    "status": "skipped",
                    "doc_id": doc_id,
                    "doc_name": path.name,
                    "reason": "already_indexed",
                    "chunks": 0,
                }
            self.vector_store.delete_by_doc_name(path.name)
        elif self._document_exists(doc_id):
            if force_reindex:
                self.vector_store.delete_document(doc_id)
            else:
                return {
                    "status": "skipped",
                    "doc_id": doc_id,
                    "doc_name": path.name,
                    "reason": "already_indexed",
                    "chunks": 0,
                }

        chunks = chunk_document(str(path), chunk_size=self.chunk_size, overlap=self.overlap)
        if not chunks:
            return {
                "status": "skipped",
                "doc_id": doc_id,
                "doc_name": path.name,
                "reason": "empty_content",
                "chunks": 0,
            }

        topic = _infer_topic(path.name)
        source_kind, source_weight = _infer_source_profile(path.name)
        normalized_chunks: List[DocumentChunk] = []
        for chunk in chunks:
            metadata = dict(chunk.metadata or {})
            metadata.update(
                {
                    "doc_id": doc_id,
                    "doc_name": path.name,
                    "source": path.name,
                    "source_path": str(path.resolve()),
                    "topic": topic,
                    "language": str(metadata.get("language") or "unknown"),
                    "token_count": int(metadata.get("token_count") or 0),
                    "source_kind": source_kind,
                    "source_weight": float(source_weight),
                }
            )
            normalized_chunks.append(DocumentChunk(content=chunk.content, metadata=metadata))

        embeddings = self.embedding_client.embed_batch(
            [chunk.content for chunk in normalized_chunks],
            task_type="RETRIEVAL_DOCUMENT",
        )
        inserted = self.vector_store.add_chunks(normalized_chunks, embeddings)

        return {
            "status": "indexed",
            "doc_id": doc_id,
            "doc_name": path.name,
            "chunks": inserted,
        }

    def ingest_directory(self, dir_path: Optional[str] = None, force_reindex: bool = False) -> Dict[str, object]:
        target = Path(dir_path or self.docs_dir)
        target.mkdir(parents=True, exist_ok=True)

        if force_reindex:
            self.vector_store.clear()

        files = sorted(
            path
            for path in target.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
        active_source_paths = {str(path.resolve()) for path in files}

        purged = 0
        if not force_reindex:
            stale_doc_names = set()
            for record in self.vector_store._records:
                metadata = record.get("metadata") if isinstance(record, dict) else {}
                metadata = metadata if isinstance(metadata, dict) else {}
                source_path = str(metadata.get("source_path") or "")
                doc_name = str(record.get("doc_name") or "")
                if not source_path or not doc_name:
                    continue
                resolved_source_path = str(Path(source_path).resolve())
                if resolved_source_path in active_source_paths:
                    continue
                if self._is_path_within_directory(resolved_source_path, target):
                    stale_doc_names.add(doc_name)
            for doc_name in stale_doc_names:
                purged += int(self.vector_store.delete_by_doc_name(doc_name) or 0)

        indexed = 0
        skipped = 0
        errors: List[Dict[str, str]] = []

        for file_path in files:
            try:
                result = self.ingest_file(str(file_path), force_reindex=False)
                if result.get("status") == "indexed":
                    indexed += 1
                else:
                    skipped += 1
            except Exception as exc:
                logging.warning("Failed to ingest %s: %s", file_path, exc)
                errors.append({"file": str(file_path), "error": str(exc)})

        return {
            "directory": str(target),
            "total_files": len(files),
            "indexed": indexed,
            "skipped": skipped,
            "purged": purged,
            "errors": errors,
            "force_reindex": force_reindex,
        }

    @staticmethod
    def _preprocess_query(question: str) -> str:
        cleaned = KnowledgeBase._strip_fillers(question)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            cleaned = (question or "").strip()

        lowered = cleaned.lower()
        expansions: List[str] = []
        seen_expansions = set()
        for keywords, expansion in _TOPIC_EXPANSION_RULES:
            if expansion in seen_expansions:
                continue
            if any(keyword in lowered for keyword in keywords):
                expansions.append(expansion)
                seen_expansions.add(expansion)

        if len(_tokenize(cleaned)) <= 12 and expansions:
            cleaned = f"{cleaned} {' '.join(expansions[:4])}".strip()
        return cleaned

    @staticmethod
    def _strip_fillers(question: str) -> str:
        return _FILLER_PATTERN.sub(" ", (question or "").strip())

    @staticmethod
    def _keyword_overlap_score(query: str, content: str) -> float:
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return 0.0
        content_lower = (content or "").lower()
        matched = sum(1 for token in query_tokens if token in content_lower)
        return matched / len(query_tokens)

    def _adaptive_fetch_k(self, query_text: str, top_k: int) -> int:
        token_count = len(_tokenize(query_text))
        adaptive = 8 + (token_count * 2)
        return max(self.base_fetch_k, top_k * 4, min(60, adaptive))

    @staticmethod
    def _result_key(result: SearchResult) -> Tuple[str, int]:
        return str(result.doc_id), int(result.chunk_index)

    def _topic_boost(self, metadata: Dict[str, object], topic_hint: Optional[str]) -> float:
        if not topic_hint:
            return 0.0
        topic = str(metadata.get("topic") or "")
        return 0.08 if topic == topic_hint else 0.0

    @staticmethod
    def _content_penalty(content: str, metadata: Dict[str, object]) -> float:
        penalty = 0.0
        section_title = str(metadata.get("section_title") or "").strip().lower()
        if section_title in _LOW_SIGNAL_SECTION_TITLES:
            penalty += 0.06
        token_count = int(metadata.get("token_count") or 0)
        if token_count and token_count < 12:
            penalty += 0.04
        unique_tokens = len(set(_tokenize(content)))
        if unique_tokens < 6:
            penalty += 0.03
        return penalty

    def _select_diverse_results(
        self,
        reranked: List[Tuple[float, float, float, SearchResult]],
        top_k: int,
    ) -> List[Tuple[float, float, float, SearchResult]]:
        selected: List[Tuple[float, float, float, SearchResult]] = []
        remaining = list(reranked)
        selected_by_doc: Dict[str, int] = {}

        while remaining and len(selected) < top_k:
            best_index = -1
            best_item: Optional[Tuple[float, float, float, SearchResult]] = None
            best_adjusted = float("-inf")

            for index, item in enumerate(remaining):
                base_score, dense_norm, lexical_norm, result = item
                doc_count = selected_by_doc.get(str(result.doc_id), 0)
                if doc_count >= self.max_chunks_per_doc:
                    continue
                adjusted_score = base_score - (self.doc_reuse_penalty * doc_count)
                if adjusted_score > best_adjusted:
                    best_adjusted = adjusted_score
                    best_index = index
                    best_item = (adjusted_score, dense_norm, lexical_norm, result)

            if best_item is None or best_index < 0:
                break

            remaining.pop(best_index)
            selected.append(best_item)
            selected_by_doc[str(best_item[3].doc_id)] = selected_by_doc.get(str(best_item[3].doc_id), 0) + 1

        return selected

    @staticmethod
    def _normalize_lexical_scores(scores: Dict[Tuple[str, int], float]) -> Dict[Tuple[str, int], float]:
        if not scores:
            return {}
        max_score = max(scores.values())
        if max_score <= 0:
            return {key: 0.0 for key in scores}
        return {key: value / max_score for key, value in scores.items()}

    def query_with_sources(self, question: str, top_k: int = 3, topic_hint: Optional[str] = None) -> Dict[str, object]:
        question = (question or "").strip()
        if not question:
            return {"context": "", "sources": []}

        top_k = max(1, int(top_k))
        base_query = self._strip_fillers(question)
        processed_query = self._preprocess_query(question)
        fetch_k = self._adaptive_fetch_k(processed_query, top_k)
        logging.debug(
            "RAG query preprocessed: '%s' -> '%s' (top_k=%s, fetch_k=%s)",
            question[:80], processed_query[:120], top_k, fetch_k,
        )

        dense_results: List[SearchResult] = []
        lexical_results: List[SearchResult] = []

        try:
            query_embedding = self.embedding_client.embed_text(
                processed_query,
                task_type="RETRIEVAL_QUERY",
            )
            dense_results = self.vector_store.search_dense(query_embedding, top_k=fetch_k)
        except Exception as exc:
            logging.warning("Dense retrieval failed: %s", exc)

        try:
            lexical_results = self.vector_store.search_lexical(processed_query, top_k=fetch_k)
        except Exception as exc:
            logging.warning("Lexical retrieval failed: %s", exc)

        if not dense_results and not lexical_results:
            return {"context": "", "sources": []}

        dense_score_map: Dict[Tuple[str, int], float] = {}
        lexical_score_map: Dict[Tuple[str, int], float] = {}
        result_map: Dict[Tuple[str, int], SearchResult] = {}

        for result in dense_results:
            key = self._result_key(result)
            dense_score_map[key] = max(dense_score_map.get(key, -1.0), float(result.score))
            result_map[key] = result

        for result in lexical_results:
            key = self._result_key(result)
            lexical_score_map[key] = max(lexical_score_map.get(key, 0.0), float(result.score))
            result_map.setdefault(key, result)

        normalized_lexical = self._normalize_lexical_scores(lexical_score_map)

        reranked: List[Tuple[float, float, float, SearchResult]] = []
        for key, result in result_map.items():
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            dense_raw = dense_score_map.get(key, -1.0)
            dense_norm = max(0.0, min(1.0, (dense_raw + 1.0) / 2.0))
            lexical_norm = max(0.0, min(1.0, normalized_lexical.get(key, 0.0)))
            keyword_norm = self._keyword_overlap_score(base_query or question, result.content)
            source_weight = float(metadata.get("source_weight") or 1.0)
            content_penalty = self._content_penalty(result.content, metadata)

            combined = (
                self.dense_weight * dense_norm
                + self.lexical_weight * lexical_norm
                + self.keyword_weight * keyword_norm
                + self._topic_boost(metadata, topic_hint=topic_hint)
            )
            combined = max(0.0, (combined * source_weight) - content_penalty)

            if (
                dense_norm < self.min_score
                and lexical_norm < self.lexical_min_score
                and keyword_norm < 0.40
            ):
                continue

            reranked.append((combined, dense_norm, lexical_norm, result))

        if not reranked:
            return {"context": "", "sources": []}

        reranked.sort(key=lambda item: item[0], reverse=True)
        selected = self._select_diverse_results(reranked, top_k)

        context_lines: List[str] = []
        sources: List[Dict[str, object]] = []
        used_chars = 0

        for index, (combined, dense_norm, lexical_norm, result) in enumerate(selected, start=1):
            metadata = result.metadata if isinstance(result.metadata, dict) else {}
            content = str(result.content or "").strip()
            if len(content) > self.max_chunk_chars:
                content = f"{content[: self.max_chunk_chars].rstrip()} ..."

            page_start = metadata.get("page_start")
            page_end = metadata.get("page_end")
            section_title = str(metadata.get("section_title") or "").strip()
            language = str(metadata.get("language") or "unknown")
            source_kind = str(metadata.get("source_kind") or "unknown")

            location_bits: List[str] = []
            if page_start and page_end and page_start == page_end:
                location_bits.append(f"page {page_start}")
            elif page_start and page_end:
                location_bits.append(f"pages {page_start}-{page_end}")
            elif page_start:
                location_bits.append(f"page {page_start}")
            if section_title:
                location_bits.append(f"section {section_title}")
            location_label = ", ".join(location_bits)
            location_suffix = f", {location_label}" if location_label else ""

            line = (
                f"[{index}] source: {result.doc_name} (score={combined:.3f}, dense={dense_norm:.3f}, lexical={lexical_norm:.3f}, kind={source_kind}{location_suffix}, lang={language})\n"
                f"{content}"
            )
            projected_chars = used_chars + len(line) + (2 if context_lines else 0)
            if projected_chars > self.max_context_chars and context_lines:
                break
            context_lines.append(line)
            used_chars = projected_chars

            sources.append(
                {
                    "doc_id": result.doc_id,
                    "doc_name": result.doc_name,
                    "chunk_index": result.chunk_index,
                    "score": round(float(combined), 4),
                    "dense_score": round(float(dense_norm), 4),
                    "lexical_score": round(float(lexical_norm), 4),
                    "source_path": str(metadata.get("source_path") or ""),
                    "page_start": metadata.get("page_start"),
                    "page_end": metadata.get("page_end"),
                    "section_title": section_title,
                    "language": language,
                    "source_kind": source_kind,
                }
            )

        return {
            "context": "\n\n".join(context_lines),
            "sources": sources,
        }

    def query(self, question: str, top_k: int = 3) -> str:
        return str(self.query_with_sources(question, top_k=top_k).get("context") or "")

    def delete_document(self, doc_id: str) -> bool:
        return self.vector_store.delete_document(doc_id)

    def get_stats(self) -> Dict[str, object]:
        stats = self.vector_store.get_stats()
        stats.update(
            {
                "docs_dir": self.docs_dir,
                "chunk_size": self.chunk_size,
                "overlap": self.overlap,
                "base_fetch_k": self.base_fetch_k,
                "max_context_chars": self.max_context_chars,
            }
        )
        return stats


_KNOWLEDGE_BASE_INSTANCE: Optional[KnowledgeBase] = None
_KNOWLEDGE_BASE_LOCK = threading.Lock()


def init_knowledge_base(
    db_manager,
    redis_client=None,
    docs_dir: str = "knowledge_docs",
    enabled: bool = True,
    auto_ingest: bool = True,
    chunk_size: int = 1100,
    overlap: int = 120,
    min_score: float = 0.42,
    embedding_dim: int = 1536,
    base_fetch_k: int = 36,
    max_context_chars: int = 5200,
) -> Optional[KnowledgeBase]:
    global _KNOWLEDGE_BASE_INSTANCE

    if not enabled:
        logging.info("RAG is disabled by configuration")
        _KNOWLEDGE_BASE_INSTANCE = None
        return None

    with _KNOWLEDGE_BASE_LOCK:
        if _KNOWLEDGE_BASE_INSTANCE is None:
            try:
                _KNOWLEDGE_BASE_INSTANCE = KnowledgeBase(
                    db_manager=db_manager,
                    redis_client=redis_client,
                    docs_dir=docs_dir,
                    chunk_size=chunk_size,
                    overlap=overlap,
                    embedding_dim=embedding_dim,
                    min_score=min_score,
                    base_fetch_k=base_fetch_k,
                    max_context_chars=max_context_chars,
                )
            except Exception as exc:
                logging.warning("Knowledge base initialization failed: %s", exc)
                _KNOWLEDGE_BASE_INSTANCE = None
                return None

    if auto_ingest and _KNOWLEDGE_BASE_INSTANCE is not None:
        try:
            _KNOWLEDGE_BASE_INSTANCE.ingest_directory(_KNOWLEDGE_BASE_INSTANCE.docs_dir)
        except Exception as exc:
            logging.warning("Knowledge base auto-ingest failed: %s", exc)

    return _KNOWLEDGE_BASE_INSTANCE


def get_knowledge_base() -> Optional[KnowledgeBase]:
    return _KNOWLEDGE_BASE_INSTANCE
