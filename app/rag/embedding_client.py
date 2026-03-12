"""Embedding client with OpenRouter primary, Gemini fallback, and Redis cache."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import List, Optional

import requests
from openai import OpenAI

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


class GeminiAPIError(RuntimeError):
    """Structured Gemini API error with HTTP status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Gemini API error {status_code}: {message}")
        self.status_code = status_code


class EmbeddingClient:
    """Generate text embeddings with low-memory API calls."""

    def __init__(
        self,
        redis_client=None,
        openrouter_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        gemini_model: Optional[str] = None,
        openai_model: str = "text-embedding-3-small",
        openrouter_base_url: str = _OPENROUTER_BASE_URL,
        embedding_dim: int = 1536,
        requests_per_second: float = 5.0,
        cache_ttl_seconds: int = 3600,
        timeout: float = 20.0,
        max_retries: int = 3,
        strict_dimension: bool = True,
    ):
        self.redis_client = redis_client
        self.openrouter_api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.openrouter_model = (
            openrouter_model
            or os.getenv("OPENROUTER_EMBEDDING_MODEL", "").strip()
            or _OPENROUTER_DEFAULT_EMBEDDING_MODEL
        )
        self.gemini_model = (
            gemini_model
            or os.getenv("GEMINI_EMBEDDING_MODEL", "").strip()
            or "gemini-embedding-001"
        )
        self.openai_model = openai_model
        self.openrouter_base_url = openrouter_base_url
        self.embedding_dim = max(1, int(embedding_dim))
        self.strict_dimension = bool(strict_dimension)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))

        self._min_interval = 1.0 / max(requests_per_second, 0.1)
        self._last_call_ts = 0.0
        self._rate_lock = threading.Lock()

        self._openrouter_client: Optional[OpenAI] = None
        if self.openrouter_api_key:
            self._openrouter_client = OpenAI(
                api_key=self.openrouter_api_key,
                base_url=self.openrouter_base_url,
            )

        self._openai_client: Optional[OpenAI] = None
        if self.openai_api_key:
            self._openai_client = OpenAI(api_key=self.openai_api_key)

        if not self.openrouter_api_key and not self.gemini_api_key and not self.openai_api_key:
            raise ValueError(
                "EmbeddingClient requires OPENROUTER_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY"
            )

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return isinstance(exc, GeminiAPIError) and exc.status_code == 404

    @staticmethod
    def _is_output_dimensionality_unsupported_error(exc: Exception) -> bool:
        if not isinstance(exc, GeminiAPIError) or exc.status_code != 400:
            return False
        message = str(exc).lower()
        return (
            "outputdimensionality" in message
            or "output dimensionality" in message
            or ("unknown name" in message and "dimension" in message)
        )

    @staticmethod
    def _extract_gemini_error_message(response) -> str:
        try:
            payload = response.json()
        except Exception:
            return ""

        if not isinstance(payload, dict):
            return ""

        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            message = error_obj.get("message") or error_obj.get("status")
            return str(message or "")
        if error_obj:
            return str(error_obj)
        return ""

    def _build_gemini_url(self, model_name: str, operation: str) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:{operation}?key={self.gemini_api_key}"
        )

    def _gemini_model_candidates(self) -> List[str]:
        candidates = [
            self.gemini_model,
            os.getenv("GEMINI_EMBEDDING_MODEL", "").strip(),
            "gemini-embedding-001",
            "text-embedding-004",
            "embedding-001",
        ]

        unique: List[str] = []
        for model_name in candidates:
            normalized = (model_name or "").strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    def _post_gemini_with_retry(self, url: str, payload: dict) -> dict:
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            self._throttle()

            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise

            if response.status_code == 429 and attempt < self.max_retries - 1:
                sleep_seconds = min(2 ** attempt, 5)
                logging.warning("Gemini rate-limited (429). Retrying in %ss", sleep_seconds)
                time.sleep(sleep_seconds)
                continue

            if response.status_code >= 400:
                message = self._extract_gemini_error_message(response) or "request failed"
                api_error = GeminiAPIError(response.status_code, message)
                last_error = api_error

                # Do not retry permanent client errors (except 429 handled above).
                if response.status_code in {400, 401, 403, 404}:
                    raise api_error

                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise api_error

            try:
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise RuntimeError("Gemini embedding response is not valid JSON") from exc

        if last_error:
            raise last_error
        raise RuntimeError("Gemini request failed without explicit exception")

    def _throttle(self) -> None:
        with self._rate_lock:
            now = time.time()
            wait = self._min_interval - (now - self._last_call_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_call_ts = time.time()

    def _cache_key(self, text: str, task_type: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"rag:embedding:{self.embedding_dim}:{task_type}:{digest}"

    def _get_cached_embedding(self, cache_key: str) -> Optional[List[float]]:
        if self.redis_client is None:
            return None

        try:
            cached = self.redis_client.get(cache_key)
            if not cached:
                return None
            parsed = json.loads(cached)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        except Exception as exc:
            logging.debug("Embedding cache read failed: %s", exc)
        return None

    def _set_cached_embedding(self, cache_key: str, embedding: List[float]) -> None:
        if self.redis_client is None:
            return

        try:
            self.redis_client.setex(cache_key, self.cache_ttl_seconds, json.dumps(embedding))
        except Exception as exc:
            logging.debug("Embedding cache write failed: %s", exc)

    def _coerce_dimension(self, embedding: List[float]) -> List[float]:
        values = [float(v) for v in embedding]
        if len(values) == self.embedding_dim:
            return values

        if self.strict_dimension:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {self.embedding_dim}, got {len(values)}"
            )

        if len(values) > self.embedding_dim:
            logging.warning(
                "Embedding dimension %s > expected %s, truncating",
                len(values),
                self.embedding_dim,
            )
            return values[: self.embedding_dim]

        logging.warning(
            "Embedding dimension %s < expected %s, zero-padding",
            len(values),
            self.embedding_dim,
        )
        return values + ([0.0] * (self.embedding_dim - len(values)))

    def _embed_text_gemini(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        last_error: Optional[Exception] = None

        for model_name in self._gemini_model_candidates():
            url = self._build_gemini_url(model_name, "embedContent")
            payload = {
                "model": f"models/{model_name}",
                "content": {
                    "parts": [{"text": text}],
                },
                "taskType": task_type,
            }
            payload_with_dimension = {
                **payload,
                "outputDimensionality": int(self.embedding_dim),
            }

            try:
                try:
                    body = self._post_gemini_with_retry(url, payload_with_dimension)
                except Exception as exc:
                    if self._is_output_dimensionality_unsupported_error(exc):
                        logging.info(
                            "Gemini model %s does not support outputDimensionality; retrying default dimension",
                            model_name,
                        )
                        body = self._post_gemini_with_retry(url, payload)
                    else:
                        raise

                embedding = body.get("embedding", {}).get("values", [])
                if not embedding:
                    raise RuntimeError("Gemini embedding response missing values")

                if model_name != self.gemini_model:
                    logging.info("Switching Gemini embedding model to %s", model_name)
                    self.gemini_model = model_name

                return [float(v) for v in embedding]
            except Exception as exc:
                last_error = exc
                if self._is_not_found_error(exc):
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("No Gemini embedding model candidates available")

    def _embed_batch_gemini(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        if not texts:
            return []

        last_error: Optional[Exception] = None

        for model_name in self._gemini_model_candidates():
            url = self._build_gemini_url(model_name, "batchEmbedContents")
            requests_payload = [
                {
                    "model": f"models/{model_name}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                }
                for text in texts
            ]
            payload = {
                "requests": requests_payload
            }
            payload_with_dimension = {
                "requests": [
                    {
                        **request_payload,
                        "outputDimensionality": int(self.embedding_dim),
                    }
                    for request_payload in requests_payload
                ]
            }

            try:
                try:
                    body = self._post_gemini_with_retry(url, payload_with_dimension)
                except Exception as exc:
                    if self._is_output_dimensionality_unsupported_error(exc):
                        logging.info(
                            "Gemini model %s does not support outputDimensionality for batch; retrying default dimension",
                            model_name,
                        )
                        body = self._post_gemini_with_retry(url, payload)
                    else:
                        raise

                embeddings = body.get("embeddings", [])
                if len(embeddings) != len(texts):
                    raise RuntimeError("Gemini batch embedding size mismatch")

                parsed: List[List[float]] = []
                for emb in embeddings:
                    values = emb.get("values", []) if isinstance(emb, dict) else []
                    if not values:
                        raise RuntimeError("Gemini batch embedding response missing values")
                    parsed.append([float(v) for v in values])

                if model_name != self.gemini_model:
                    logging.info("Switching Gemini embedding model to %s", model_name)
                    self.gemini_model = model_name

                return parsed
            except Exception as exc:
                last_error = exc
                if self._is_not_found_error(exc):
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("No Gemini batch embedding model candidates available")

    def _embed_text_openai(self, text: str) -> List[float]:
        if self._openai_client is None:
            raise RuntimeError("OpenAI fallback client is not configured")

        self._throttle()
        response = self._openai_client.embeddings.create(
            model=self.openai_model,
            input=text,
        )
        if not response.data:
            raise RuntimeError("OpenAI embedding response missing data")
        return [float(v) for v in response.data[0].embedding]

    @staticmethod
    def _embedding_item_values(item) -> List[float]:
        values = getattr(item, "embedding", None)
        if values is None and isinstance(item, dict):
            values = item.get("embedding")
        if not isinstance(values, list):
            return []
        return [float(v) for v in values]

    @staticmethod
    def _embedding_item_index(item) -> int:
        index = getattr(item, "index", None)
        if index is None and isinstance(item, dict):
            index = item.get("index")
        try:
            return int(index)
        except Exception:
            return 0

    def _embed_batch_openai(self, texts: List[str]) -> List[List[float]]:
        if self._openai_client is None:
            raise RuntimeError("OpenAI fallback client is not configured")
        if not texts:
            return []

        self._throttle()
        response = self._openai_client.embeddings.create(
            model=self.openai_model,
            input=texts,
        )
        if not response.data:
            raise RuntimeError("OpenAI batch embedding response missing data")
        if len(response.data) != len(texts):
            raise RuntimeError("OpenAI batch embedding size mismatch")

        ordered = sorted(response.data, key=self._embedding_item_index)
        parsed = [self._embedding_item_values(item) for item in ordered]
        if any(not emb for emb in parsed):
            raise RuntimeError("OpenAI batch embedding response missing values")
        return parsed

    def _embed_text_openrouter(self, text: str) -> List[float]:
        if self._openrouter_client is None:
            raise RuntimeError("OpenRouter embedding client is not configured")

        self._throttle()
        response = self._openrouter_client.embeddings.create(
            model=self.openrouter_model,
            input=text,
        )
        if not response.data:
            raise RuntimeError("OpenRouter embedding response missing data")
        values = self._embedding_item_values(response.data[0])
        if not values:
            raise RuntimeError("OpenRouter embedding response missing values")
        return values

    def _embed_batch_openrouter(self, texts: List[str]) -> List[List[float]]:
        if self._openrouter_client is None:
            raise RuntimeError("OpenRouter embedding client is not configured")
        if not texts:
            return []

        self._throttle()
        response = self._openrouter_client.embeddings.create(
            model=self.openrouter_model,
            input=texts,
        )
        if not response.data:
            raise RuntimeError("OpenRouter batch embedding response missing data")
        if len(response.data) != len(texts):
            raise RuntimeError("OpenRouter batch embedding size mismatch")

        ordered = sorted(response.data, key=self._embedding_item_index)
        parsed = [self._embedding_item_values(item) for item in ordered]
        if any(not emb for emb in parsed):
            raise RuntimeError("OpenRouter batch embedding response missing values")
        return parsed

    def _embed_text_with_fallback_priority(
        self,
        text: str,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[float]:
        last_error: Optional[Exception] = None

        if self.openrouter_api_key:
            try:
                return self._coerce_dimension(self._embed_text_openrouter(text))
            except Exception as exc:
                last_error = exc
                logging.warning("OpenRouter embedding failed, trying Gemini fallback: %s", exc)

        if self.gemini_api_key:
            try:
                return self._coerce_dimension(self._embed_text_gemini(text, task_type=task_type))
            except Exception as exc:
                last_error = exc
                logging.warning("Gemini embedding failed, trying OpenAI fallback: %s", exc)

        if self.openai_api_key:
            try:
                return self._coerce_dimension(self._embed_text_openai(text))
            except Exception as exc:
                last_error = exc
                logging.warning("OpenAI embedding failed: %s", exc)

        if last_error:
            raise RuntimeError("No available embedding provider succeeded") from last_error
        raise RuntimeError("No embedding provider is configured")

    def embed_text(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        """Embed one text with cache and provider fallback priority."""
        normalized = (text or "").strip()
        if not normalized:
            return [0.0] * self.embedding_dim

        cache_key = self._cache_key(normalized, task_type)
        cached = self._get_cached_embedding(cache_key)
        if cached is not None:
            return self._coerce_dimension(cached)

        final_embedding = self._embed_text_with_fallback_priority(normalized, task_type=task_type)
        self._set_cached_embedding(cache_key, final_embedding)
        return final_embedding

    def embed_batch(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        """Embed a batch with cache reuse and provider fallback priority."""
        if not texts:
            return []

        final_embeddings: List[Optional[List[float]]] = [None] * len(texts)
        pending_indices: List[int] = []
        pending_texts: List[str] = []

        for index, text in enumerate(texts):
            normalized = (text or "").strip()
            if not normalized:
                final_embeddings[index] = [0.0] * self.embedding_dim
                continue

            cache_key = self._cache_key(normalized, task_type)
            cached = self._get_cached_embedding(cache_key)
            if cached is not None:
                final_embeddings[index] = self._coerce_dimension(cached)
                continue

            pending_indices.append(index)
            pending_texts.append(normalized)

        if pending_texts:
            pending_embeddings: Optional[List[List[float]]] = None

            if self.openrouter_api_key:
                try:
                    candidate_embeddings = self._embed_batch_openrouter(pending_texts)
                    pending_embeddings = [self._coerce_dimension(embedding) for embedding in candidate_embeddings]
                except Exception as exc:
                    logging.warning("OpenRouter batch embedding failed, trying Gemini fallback: %s", exc)

            if pending_embeddings is None and self.gemini_api_key:
                try:
                    candidate_embeddings = self._embed_batch_gemini(pending_texts, task_type=task_type)
                    pending_embeddings = [self._coerce_dimension(embedding) for embedding in candidate_embeddings]
                except Exception as exc:
                    logging.warning("Gemini batch embedding failed, trying OpenAI fallback: %s", exc)

            if pending_embeddings is None and self.openai_api_key:
                try:
                    candidate_embeddings = self._embed_batch_openai(pending_texts)
                    pending_embeddings = [self._coerce_dimension(embedding) for embedding in candidate_embeddings]
                except Exception as exc:
                    logging.warning("OpenAI batch embedding failed, fallback mode: %s", exc)

            if pending_embeddings is None:
                pending_embeddings = [
                    self._embed_text_with_fallback_priority(text, task_type=task_type)
                    for text in pending_texts
                ]

            for index, text, embedding in zip(pending_indices, pending_texts, pending_embeddings):
                final_embedding = self._coerce_dimension(embedding)
                final_embeddings[index] = final_embedding
                self._set_cached_embedding(self._cache_key(text, task_type), final_embedding)

        return [embedding or ([0.0] * self.embedding_dim) for embedding in final_embeddings]
