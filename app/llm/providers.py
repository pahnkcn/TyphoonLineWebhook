"""
Multi-AI Provider Registry

Auto-discovers configured AI providers from environment variables.
All providers use the OpenAI SDK compatibility layer.
"""
import os
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

logger = logging.getLogger(__name__)

# Connection pooling
_client_cache: Dict[str, OpenAI] = {}
_cache_lock = threading.Lock()

# Default timeout and retry settings
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_RETRIES = 5

# OpenRouter constants
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_DEFAULT_MODEL = "openai/gpt-4o"
_OPENROUTER_CLIENT_KEY = "_openrouter_shared_"


@dataclass
class AIProvider:
    """Configuration for a single AI provider."""
    name: str
    api_key_env: str
    base_url: str
    default_model: str
    model_env: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    enabled: bool = False

    def __post_init__(self):
        self.api_key = os.getenv(self.api_key_env, "").strip()
        if self.model_env:
            self.model = os.getenv(self.model_env, self.default_model).strip()
        else:
            self.model = self.default_model
        self.enabled = bool(self.api_key)


# Provider definitions — order determines tie-breaking priority
PROVIDER_DEFINITIONS: List[Dict] = [
    {
        "name": "chatgpt",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "model_env": "OPENAI_MODEL",
    },
    {
        "name": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "model_env": "GEMINI_MODEL",
    },
    {
        "name": "grok",
        "api_key_env": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-4-1-fast-non-reasoning",
        "model_env": "XAI_MODEL",
    },
    {
        "name": "deepseek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "model_env": "DEEPSEEK_MODEL",
    },
    {
        "name": "kimi",
        "api_key_env": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.ai/v1",
        "default_model": "moonshot-v1-8k",
        "model_env": "MOONSHOT_MODEL",
    },
]


class ProviderRegistry:
    """Manages AI providers and their OpenAI-compatible clients."""

    def __init__(self):
        self._providers: Dict[str, AIProvider] = {}
        self._discover_providers()
        self._discover_openrouter_providers()

    def _discover_providers(self):
        """Auto-discover direct providers from environment variables."""
        for defn in PROVIDER_DEFINITIONS:
            provider = AIProvider(**defn)
            if provider.enabled:
                self._providers[provider.name] = provider
                logger.info(
                    f"Multi-AI provider discovered: {provider.name} "
                    f"(model={provider.model})"
                )
            else:
                logger.debug(
                    f"Multi-AI provider skipped (no API key): {provider.name} "
                    f"(env={provider.api_key_env})"
                )

    def _discover_openrouter_providers(self):
        """Auto-discover OpenRouter virtual providers.

        Reads OPENROUTER_API_KEY and OPENROUTER_MODELS (comma-separated).
        Each model becomes a virtual provider named 'openrouter:{model_id}'.
        All virtual providers share a single OpenAI client.
        """
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            logger.debug("Multi-AI provider skipped (no API key): openrouter (env=OPENROUTER_API_KEY)")
            return

        models_raw = os.getenv("OPENROUTER_MODELS", "").strip()
        if models_raw:
            models = [m.strip() for m in models_raw.split(",") if m.strip()]
        else:
            models = [_OPENROUTER_DEFAULT_MODEL]

        for model_id in models:
            name = f"openrouter:{model_id}"
            provider = AIProvider(
                name=name,
                api_key_env="OPENROUTER_API_KEY",
                base_url=_OPENROUTER_BASE_URL,
                default_model=model_id,
            )
            # api_key is already resolved by __post_init__
            self._providers[name] = provider
            logger.info(
                f"Multi-AI provider discovered: {name} "
                f"(model={model_id}, via OpenRouter)"
            )

        logger.info(f"OpenRouter: {len(models)} model(s) registered")

        # Log total after both discovery phases
        logger.info(
            f"Multi-AI: {len(self._providers)} provider(s) active: "
            f"{list(self._providers.keys())}"
        )

    def get_active_providers(self) -> List[AIProvider]:
        """Return list of all enabled providers."""
        return list(self._providers.values())

    def get_provider(self, name: str) -> Optional[AIProvider]:
        """Get a specific provider by name."""
        return self._providers.get(name)

    def get_client(self, provider: AIProvider) -> OpenAI:
        """Get or create a cached OpenAI client for a provider.

        Thread-safe with locking. Reuses connections for performance.
        OpenRouter virtual providers share a single client.
        """
        # OpenRouter providers share one client (same key + base_url)
        cache_key = (
            _OPENROUTER_CLIENT_KEY
            if provider.name.startswith("openrouter:")
            else provider.name
        )

        with _cache_lock:
            if cache_key not in _client_cache:
                client = OpenAI(
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    max_retries=_DEFAULT_MAX_RETRIES,
                    timeout=_DEFAULT_TIMEOUT,
                )
                _client_cache[cache_key] = client
                logger.debug(
                    f"Created OpenAI client for {cache_key} "
                    f"(base_url={provider.base_url})"
                )
            return _client_cache[cache_key]

    @property
    def count(self) -> int:
        """Number of active providers."""
        return len(self._providers)

    def clear_cache(self):
        """Clear all cached clients."""
        with _cache_lock:
            _client_cache.clear()
            logger.info("Cleared multi-AI client cache")


_registry: Optional[ProviderRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ProviderRegistry:
    """Get or create the global provider registry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ProviderRegistry()
    return _registry


def reset_registry():
    """Reset the global registry (for testing)."""
    global _registry
    with _registry_lock:
        _registry = None
    with _cache_lock:
        _client_cache.clear()
