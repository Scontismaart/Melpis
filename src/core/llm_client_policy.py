"""Shared, server-only policy for constructing generation clients."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.core.ai_providers import ProviderAdapter, provider_for_model
from src.core.cost_policy import assert_model_allowed, assert_openrouter_catalog_free, free_only


@dataclass(frozen=True)
class LLMClientPolicy:
    """Validated inputs needed to construct one provider client.

    Instances are server-side configuration objects. Secret-bearing fields are
    excluded from repr output so accidental diagnostic logging cannot expose
    keys or custom endpoints.
    """

    provider: ProviderAdapter
    model: str
    api_key: str = field(repr=False)
    base_url: str | None = field(repr=False)

    def client_params(self) -> dict[str, object]:
        """Return the provider's constructor arguments, including its secret key."""
        return self.provider.client_params(self.model, self.api_key, self.base_url)


def resolve_llm_client_policy(model: str) -> LLMClientPolicy:
    """Validate and resolve existing environment-backed generation policy."""
    assert_model_allowed(model)

    configured_provider = os.getenv("AI_PROVIDER", "").strip().lower()
    provider = provider_for_model(model, configured_provider)
    free_profile = free_only()
    base_url = os.getenv("AI_BASE_URL", "").strip() or None

    if free_profile and (provider.name not in {"groq", "openrouter"} or base_url):
        raise RuntimeError("Budget EUR 0: provider o endpoint non autorizzato")

    key_env = provider.key_env
    if provider.name == "openai_compatible" or (os.getenv("AI_PROVIDER") and not free_profile):
        key_env = "AI_API_KEY" if os.getenv("AI_API_KEY", "").strip() else provider.key_env
    api_key = os.getenv(key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{key_env} non trovata. Copia .env.example in .env e inserisci "
            f"la chiave API per il provider '{provider.name}'."
        )

    if free_profile and provider.name == "openrouter":
        assert_openrouter_catalog_free(model, api_key)

    # Validate endpoint rules here, before returning a policy that could be
    # mistaken for permission to construct a client.
    provider.client_params(model, api_key, base_url)
    return LLMClientPolicy(provider=provider, model=model, api_key=api_key, base_url=base_url)
