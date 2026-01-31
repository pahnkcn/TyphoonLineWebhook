import os
import logging
import threading
from typing import Iterable, AsyncIterable, List, Dict, Any, Optional, Tuple

from openai import OpenAI, AsyncOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    APIStatusError,
)


class GrokAPIError(Exception):
    """Custom exception for Grok API validation errors"""
    pass


# Defaults per xAI docs: use OpenAI client with base_url to xAI
_DEFAULT_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning")


def _filter_unsupported_params(model: str, params: Dict[str, Any]) -> Dict[str, Any]:
    model_lower = (model or "").lower()
    if "reasoning" not in model_lower:
        return params

    unsupported = {
        "presence_penalty",
        "presencePenalty",
        "frequency_penalty",
        "frequencyPenalty",
    }
    removed = [k for k in params.keys() if k in unsupported]
    if not removed:
        return params

    filtered = {k: v for k, v in params.items() if k not in unsupported}
    logging.debug(f"Removed unsupported params for model={model}: {removed}")
    return filtered


# Client connection pooling - reuse connections for better performance
_sync_client_cache: Dict[Tuple[str, str], OpenAI] = {}
_async_client_cache: Dict[Tuple[str, str], AsyncOpenAI] = {}
_cache_lock = threading.Lock()

# Configuration for connection pooling
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_TIMEOUT = 60.0  # seconds


def _get_cache_key(api_key: Optional[str], base_url: Optional[str]) -> Tuple[str, str]:
    """Generate cache key for client pooling"""
    key = api_key or os.getenv("XAI_API_KEY", "")
    url = base_url or os.getenv("XAI_BASE_URL", _DEFAULT_BASE_URL)
    return (key, url)


def _get_sync_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> OpenAI:
    """
    Get or create a cached synchronous OpenAI client.

    Uses connection pooling to reuse TCP connections and reduce latency.
    Thread-safe with locking.

    Args:
        api_key: Optional API key (defaults to XAI_API_KEY env var)
        base_url: Optional base URL (defaults to XAI_BASE_URL env var or xAI default)

    Returns:
        Cached or new OpenAI client instance
    """
    cache_key = _get_cache_key(api_key, base_url)

    with _cache_lock:
        if cache_key not in _sync_client_cache:
            client = OpenAI(
                api_key=cache_key[0],
                base_url=cache_key[1],
                max_retries=_DEFAULT_MAX_RETRIES,
                timeout=_DEFAULT_TIMEOUT,
            )
            _sync_client_cache[cache_key] = client
            logging.debug(f"Created new sync Grok client (cache size: {len(_sync_client_cache)})")

        return _sync_client_cache[cache_key]


def _get_async_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> AsyncOpenAI:
    """
    Get or create a cached asynchronous OpenAI client.

    Uses connection pooling to reuse TCP connections and reduce latency.
    Thread-safe with locking.

    Args:
        api_key: Optional API key (defaults to XAI_API_KEY env var)
        base_url: Optional base URL (defaults to XAI_BASE_URL env var or xAI default)

    Returns:
        Cached or new AsyncOpenAI client instance
    """
    cache_key = _get_cache_key(api_key, base_url)

    with _cache_lock:
        if cache_key not in _async_client_cache:
            client = AsyncOpenAI(
                api_key=cache_key[0],
                base_url=cache_key[1],
                max_retries=_DEFAULT_MAX_RETRIES,
                timeout=_DEFAULT_TIMEOUT,
            )
            _async_client_cache[cache_key] = client
            logging.debug(f"Created new async Grok client (cache size: {len(_async_client_cache)})")

        return _async_client_cache[cache_key]


def clear_client_cache():
    """
    Clear the client connection cache.

    Useful for testing or when you need to force new connections.
    Thread-safe operation.
    """
    with _cache_lock:
        _sync_client_cache.clear()
        _async_client_cache.clear()
        logging.info("Cleared Grok client connection cache")


def get_client_cache_stats() -> Dict[str, int]:
    """
    Get statistics about the client connection cache.

    Returns:
        Dictionary with cache statistics
    """
    with _cache_lock:
        return {
            'sync_clients': len(_sync_client_cache),
            'async_clients': len(_async_client_cache),
            'total_clients': len(_sync_client_cache) + len(_async_client_cache)
        }


def send_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Send a synchronous chat completion and return the text content.

    Parameters mirror OpenAI Chat Completions per xAI docs.

    Returns:
        str: The response content from the model

    Raises:
        GrokAPIError: If response validation fails
        APITimeoutError: If request times out
        APIConnectionError: If connection fails
        RateLimitError: If rate limit exceeded
        APIStatusError: If API returns error status
    """
    client = _get_sync_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    params = _filter_unsupported_params(str(params.get("model", "")), params)

    try:
        resp = client.chat.completions.create(**params)

        # Validate response structure
        if not resp:
            logging.error("xAI returned null response object")
            raise GrokAPIError("Received null response from xAI API")

        if not hasattr(resp, 'choices') or not resp.choices:
            logging.error("xAI returned response with no choices array")
            raise GrokAPIError("Response missing choices array")

        if len(resp.choices) == 0:
            logging.error("xAI returned empty choices array")
            raise GrokAPIError("Response contains empty choices array")

        # Validate message content
        first_choice = resp.choices[0]
        if not hasattr(first_choice, 'message'):
            logging.error("xAI response choice missing message attribute")
            raise GrokAPIError("Response choice missing message")

        content = first_choice.message.content

        if content is None:
            logging.error("xAI returned null content in message")
            raise GrokAPIError("Response content is null")

        if not isinstance(content, str):
            logging.error(f"xAI returned non-string content: {type(content)}")
            raise GrokAPIError(f"Response content has invalid type: {type(content)}")

        if not content.strip():
            logging.error("xAI returned empty/whitespace-only content")
            raise GrokAPIError("Response content is empty or whitespace-only")

        # Log successful response
        logging.debug(f"xAI response validated: {len(content)} chars, model={params['model']}")

        return content.strip()

    except GrokAPIError:
        # Re-raise validation errors as-is
        raise
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok chat error: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error in xAI chat completion: {type(e).__name__}: {e}")
        raise GrokAPIError(f"Unexpected API error: {str(e)}") from e


def stream_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Iterable[str]:
    """Synchronous streaming chat completion yielding text chunks.

    Uses Chat Completions streaming compatible with OpenAI client.

    Yields:
        str: Text chunks as they arrive from the API

    Raises:
        GrokAPIError: If stream validation fails
        APITimeoutError: If request times out
        APIConnectionError: If connection fails
        RateLimitError: If rate limit exceeded
        APIStatusError: If API returns error status
    """
    client = _get_sync_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    params = _filter_unsupported_params(str(params.get("model", "")), params)

    chunks_received = 0
    total_content_length = 0

    try:
        stream = client.chat.completions.create(**params)

        for chunk in stream:
            # Skip chunks without choices
            if not chunk.choices:
                logging.debug("Received streaming chunk with no choices")
                continue

            # Validate chunk structure
            if len(chunk.choices) == 0:
                logging.debug("Received streaming chunk with empty choices array")
                continue

            first_choice = chunk.choices[0]

            # Try to get content from delta (standard streaming format)
            delta = getattr(first_choice, "delta", None)
            if delta:
                content = getattr(delta, "content", None)
                if content and isinstance(content, str):
                    chunks_received += 1
                    total_content_length += len(content)
                    yield content
                    continue

            # Fallback: try to get content from message (some servers use this)
            message = getattr(first_choice, "message", None)
            if message:
                content = getattr(message, "content", None)
                if content and isinstance(content, str):
                    chunks_received += 1
                    total_content_length += len(content)
                    yield content

        # Log successful stream completion
        logging.info(
            f"xAI stream completed successfully: {chunks_received} chunks, "
            f"{total_content_length} chars, model={params['model']}"
        )

        # Validate that we received at least some content
        if chunks_received == 0:
            logging.warning("xAI streaming completed but received no content chunks")
            raise GrokAPIError("Stream completed with no content received")

    except GrokAPIError:
        # Re-raise validation errors as-is
        raise
    except (APITimeoutError, APIConnectionError) as e:
        error_msg = f"xAI stream interrupted after {chunks_received} chunks ({total_content_length} chars): {e}"
        logging.error(error_msg)

        # Yield error indicator if stream was partial
        if chunks_received > 0:
            yield "\n\n[⚠️ การเชื่อมต่อขัดข้อง - ข้อความอาจไม่สมบูรณ์]"

        raise
    except RateLimitError as e:
        logging.error(f"xAI rate limit during streaming after {chunks_received} chunks: {e}")
        if chunks_received > 0:
            yield "\n\n[⚠️ ระบบกำลังมีการใช้งานสูง]"
        raise
    except APIStatusError as e:
        logging.error(f"xAI API status error during streaming after {chunks_received} chunks: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error in xAI streaming after {chunks_received} chunks: {type(e).__name__}: {e}")
        if chunks_received > 0:
            yield "\n\n[⚠️ เกิดข้อผิดพลาดในการส่งข้อความ]"
        raise GrokAPIError(f"Unexpected streaming error: {str(e)}") from e


async def astream_chat(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Async chat completion returning the text content.

    Returns:
        str: The response content from the model

    Raises:
        GrokAPIError: If response validation fails
        APITimeoutError: If request times out
        APIConnectionError: If connection fails
        RateLimitError: If rate limit exceeded
        APIStatusError: If API returns error status
    """
    client = _get_async_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    params = _filter_unsupported_params(str(params.get("model", "")), params)

    try:
        resp = await client.chat.completions.create(**params)
        # Do not close client explicitly; connection pool is reused by SDK.

        # Validate response structure
        if not resp:
            logging.error("xAI async returned null response object")
            raise GrokAPIError("Received null response from xAI API")

        if not hasattr(resp, 'choices') or not resp.choices:
            logging.error("xAI async returned response with no choices array")
            raise GrokAPIError("Response missing choices array")

        if len(resp.choices) == 0:
            logging.error("xAI async returned empty choices array")
            raise GrokAPIError("Response contains empty choices array")

        # Validate message content
        first_choice = resp.choices[0]
        if not hasattr(first_choice, 'message'):
            logging.error("xAI async response choice missing message attribute")
            raise GrokAPIError("Response choice missing message")

        content = first_choice.message.content

        if content is None:
            logging.error("xAI async returned null content in message")
            raise GrokAPIError("Response content is null")

        if not isinstance(content, str):
            logging.error(f"xAI async returned non-string content: {type(content)}")
            raise GrokAPIError(f"Response content has invalid type: {type(content)}")

        if not content.strip():
            logging.error("xAI async returned empty/whitespace-only content")
            raise GrokAPIError("Response content is empty or whitespace-only")

        # Log successful response
        logging.debug(f"xAI async response validated: {len(content)} chars, model={params['model']}")

        return content.strip()

    except GrokAPIError:
        # Re-raise validation errors as-is
        raise
    except (APITimeoutError, APIConnectionError, RateLimitError, APIStatusError) as e:
        logging.error(f"xAI Grok async chat error: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error in xAI async chat completion: {type(e).__name__}: {e}")
        raise GrokAPIError(f"Unexpected async API error: {str(e)}") from e


async def astream_chat_iter(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> AsyncIterable[str]:
    """Async streaming generator yielding text chunks.

    Yields:
        str: Text chunks as they arrive from the API

    Raises:
        GrokAPIError: If stream validation fails
        APITimeoutError: If request times out
        APIConnectionError: If connection fails
        RateLimitError: If rate limit exceeded
        APIStatusError: If API returns error status
    """
    client = _get_async_client(api_key, base_url)

    params: Dict[str, Any] = {
        "model": model or _DEFAULT_MODEL,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        params["temperature"] = temperature
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if top_p is not None:
        params["top_p"] = top_p
    if presence_penalty is not None:
        params["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        params["frequency_penalty"] = frequency_penalty
    if extra:
        params.update(extra)

    params = _filter_unsupported_params(str(params.get("model", "")), params)

    chunks_received = 0
    total_content_length = 0

    try:
        stream = await client.chat.completions.create(**params)

        async for chunk in stream:
            # Skip chunks without choices
            if not chunk.choices:
                logging.debug("Received async streaming chunk with no choices")
                continue

            # Validate chunk structure
            if len(chunk.choices) == 0:
                logging.debug("Received async streaming chunk with empty choices array")
                continue

            first_choice = chunk.choices[0]

            # Try to get content from delta (standard streaming format)
            delta = getattr(first_choice, "delta", None)
            if delta:
                content = getattr(delta, "content", None)
                if content and isinstance(content, str):
                    chunks_received += 1
                    total_content_length += len(content)
                    yield content
                    continue

            # Fallback: try to get content from message (some servers use this)
            message = getattr(first_choice, "message", None)
            if message:
                content = getattr(message, "content", None)
                if content and isinstance(content, str):
                    chunks_received += 1
                    total_content_length += len(content)
                    yield content

        # Log successful stream completion
        logging.info(
            f"xAI async stream completed successfully: {chunks_received} chunks, "
            f"{total_content_length} chars, model={params['model']}"
        )

        # Validate that we received at least some content
        if chunks_received == 0:
            logging.warning("xAI async streaming completed but received no content chunks")
            raise GrokAPIError("Async stream completed with no content received")

    except GrokAPIError:
        # Re-raise validation errors as-is
        raise
    except (APITimeoutError, APIConnectionError) as e:
        error_msg = f"xAI async stream interrupted after {chunks_received} chunks ({total_content_length} chars): {e}"
        logging.error(error_msg)

        # Yield error indicator if stream was partial
        if chunks_received > 0:
            yield "\n\n[⚠️ การเชื่อมต่อขัดข้อง - ข้อความอาจไม่สมบูรณ์]"

        raise
    except RateLimitError as e:
        logging.error(f"xAI async rate limit during streaming after {chunks_received} chunks: {e}")
        if chunks_received > 0:
            yield "\n\n[⚠️ ระบบกำลังมีการใช้งานสูง]"
        raise
    except APIStatusError as e:
        logging.error(f"xAI async API status error during streaming after {chunks_received} chunks: {e}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error in xAI async streaming after {chunks_received} chunks: {type(e).__name__}: {e}")
        if chunks_received > 0:
            yield "\n\n[⚠️ เกิดข้อผิดพลาดในการส่งข้อความ]"
        raise GrokAPIError(f"Unexpected async streaming error: {str(e)}") from e

