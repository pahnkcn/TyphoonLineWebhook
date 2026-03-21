"""RAG package: document processing, embeddings, vector search, and knowledge API."""

from .document_processor import (
    DocumentChunk,
    DocumentSegment,
    load_pdf,
    load_docx,
    load_text,
    load_document,
    load_document_segments,
    chunk_text,
    chunk_document,
)
from .embedding_client import EmbeddingClient
from .vector_store import SearchResult, VectorStore
from .knowledge_base import KnowledgeBase, init_knowledge_base, get_knowledge_base

__all__ = [
    "DocumentChunk",
    "DocumentSegment",
    "load_pdf",
    "load_docx",
    "load_text",
    "load_document",
    "load_document_segments",
    "chunk_text",
    "chunk_document",
    "EmbeddingClient",
    "SearchResult",
    "VectorStore",
    "KnowledgeBase",
    "init_knowledge_base",
    "get_knowledge_base",
]
