"""
Unified AI caller with multi-AI consensus support.

Provides ``call_ai`` — a drop-in replacement for direct
``grok_client.send_chat`` calls that transparently routes through
the multi-AI consensus engine when ``MULTI_AI_ENABLED=true`` and
at least two providers are available, falling back to Grok otherwise.
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from . import grok_client
from .multi_ai import multi_ai_chat, ConsensusResult
from .providers import get_registry

logger = logging.getLogger(__name__)


def call_ai(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: float = 0,
    circuit_breaker: Optional[Any] = None,
    on_consensus: Optional[Callable[[ConsensusResult], None]] = None,
    _config: Optional[Any] = None,
    **kwargs: Any,
) -> str:
    """Send a chat completion, using multi-AI consensus when available.

    When ``MULTI_AI_ENABLED`` is *true* in the app config and the provider
    registry contains ≥ 2 providers, the request is dispatched through
    :func:`multi_ai_chat`.  Otherwise (or on failure) it falls back to
    :func:`grok_client.send_chat`.

    Args:
        messages: Conversation messages in OpenAI format.
        model: Model name passed to the single-provider fallback.
        temperature: Generation temperature.
        max_tokens: Maximum tokens for generation.
        timeout: Overall time budget in seconds (0 = default).
        circuit_breaker: Optional CircuitBreaker instance to wrap the
            single-provider (Grok) fallback call.
        on_consensus: Optional callback invoked with the
            :class:`ConsensusResult` when multi-AI consensus succeeds.
        _config: Optional pre-loaded Config object.  When *None*,
            :func:`load_config` is called automatically.
        **kwargs: Extra keyword arguments forwarded to
            ``grok_client.send_chat`` in fallback mode (e.g. ``top_p``).

    Returns:
        The AI-generated text content.
    """
    if _config is not None:
        config = _config
    else:
        # Lazy import to avoid circular dependency
        from ..config import load_config
        config = load_config()

    if getattr(config, "MULTI_AI_ENABLED", False):
        try:
            registry = get_registry()
            if registry.count >= 2:
                effective_timeout = timeout if timeout > 0 else getattr(config, "MULTI_AI_TIMEOUT", 45)
                logger.info(
                    "[call_ai] Using multi-AI consensus (%d providers, timeout=%ss)",
                    registry.count,
                    effective_timeout,
                )
                consensus = multi_ai_chat(
                    messages=messages,
                    registry=registry,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=effective_timeout,
                )
                logger.info(
                    "[call_ai] Consensus result: best=%s score=%.1f time=%.0fms",
                    consensus.best_provider,
                    consensus.avg_score,
                    consensus.total_time_ms,
                )
                if on_consensus is not None:
                    try:
                        on_consensus(consensus)
                    except Exception as cb_err:
                        logger.warning("[call_ai] on_consensus callback failed: %s", cb_err)
                return consensus.best_response
        except RuntimeError as e:
            logger.warning("[call_ai] Multi-AI failed, falling back to Grok: %s", e)
        except Exception as e:
            logger.error("[call_ai] Unexpected multi-AI error, falling back to Grok: %s", e)

    # Single-provider fallback
    grok_kwargs = dict(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens, **kwargs)

    if circuit_breaker is not None:
        return circuit_breaker.call(grok_client.send_chat, **grok_kwargs)

    return grok_client.send_chat(**grok_kwargs)
