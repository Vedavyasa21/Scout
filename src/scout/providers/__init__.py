"""Provider factory.

Application code depends on `ModelProvider` only; adding a backend means
adding a module here and a branch in `create_provider`.
"""

from __future__ import annotations

from scout.config import ConfigurationError, MissingCredentialError, Settings
from scout.providers.base import (
    AssistantTurn,
    ModelProvider,
    ProviderCapabilities,
    ProviderChunk,
    ProviderError,
    ProviderReply,
    ToolCall,
    ToolTurn,
    Turn,
    UserTurn,
)

SUPPORTED_PROVIDERS = ("gemini",)


def create_provider(settings: Settings) -> ModelProvider:
    """Instantiate the configured provider with validated credentials."""
    provider = settings.provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(
            f"Unsupported provider '{provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    api_key = settings.require_api_key()
    if provider == "gemini":
        from scout.providers.gemini import GeminiProvider

        return GeminiProvider(
            model=settings.model,
            api_key=api_key,
            request_timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            thinking_budget=settings.thinking_budget,
            thinking_level=settings.thinking_level,
        )
    raise ConfigurationError(f"Unsupported provider '{provider}'")  # pragma: no cover


__all__ = [
    "SUPPORTED_PROVIDERS",
    "AssistantTurn",
    "ConfigurationError",
    "MissingCredentialError",
    "ModelProvider",
    "ProviderCapabilities",
    "ProviderChunk",
    "ProviderError",
    "ProviderReply",
    "ToolCall",
    "ToolTurn",
    "Turn",
    "UserTurn",
    "create_provider",
]
