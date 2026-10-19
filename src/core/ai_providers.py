"""Provider adapters for the common CrewAI/LiteLLM chat interface.

Adapters only construct clients. Cost authorization is enforced separately,
before a provider key or endpoint is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    key_env: str
    model_prefix: str = ""
    base_url: str | None = None
    requires_base_url: bool = False
    deny_training: bool = False

    def client_params(self, model: str, api_key: str, base_url: str | None) -> dict[str, object]:
        params: dict[str, object] = {
            "model": model,
            "api_key": api_key,
        }
        if self.model_prefix and not model.startswith(self.model_prefix):
            params["model"] = f"{self.model_prefix}{model}"
        endpoint = base_url or self.base_url
        if self.requires_base_url and not endpoint:
            raise RuntimeError("AI_BASE_URL richiesta per AI_PROVIDER=openai_compatible")
        if base_url and self.name != "openai_compatible":
            raise RuntimeError(f"AI_BASE_URL non supportata da AI_PROVIDER={self.name}")
        if endpoint:
            try:
                parsed = urlparse(endpoint)
                hostname = parsed.hostname
            except ValueError as exc:
                raise RuntimeError("AI_BASE_URL non valida") from exc
            if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
                raise RuntimeError("AI_BASE_URL non valida")
            if parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise RuntimeError("AI_BASE_URL remota deve usare HTTPS")
            params["base_url"] = endpoint
        if self.deny_training:
            params["additional_params"] = {
                "extra_body": {"provider": {
                    "data_collection": "deny",
                    "zdr": True,
                    # Do not let upstream routing escape the explicitly
                    # selected model/provider policy (including :free).
                    "allow_fallbacks": False,
                }},
            }
        return params


PROVIDERS: dict[str, ProviderAdapter] = {
    "groq": ProviderAdapter("groq", "GROQ_API_KEY", "groq/"),
    "openrouter": ProviderAdapter(
        "openrouter", "OPENROUTER_API_KEY", "openrouter/",
        "https://openrouter.ai/api/v1", deny_training=True,
    ),
    "openai_compatible": ProviderAdapter(
        "openai_compatible", "AI_API_KEY", "openai/", requires_base_url=True,
    ),
    "cerebras": ProviderAdapter("cerebras", "CEREBRAS_API_KEY", "cerebras/"),
    "mistral": ProviderAdapter("mistral", "MISTRAL_API_KEY", "mistral/"),
}


def provider_for_model(model: str, configured: str = "") -> ProviderAdapter:
    """Honor an explicit provider, preserving legacy model-prefix inference."""
    if configured:
        adapter = PROVIDERS.get(configured)
        if adapter is None:
            raise RuntimeError(f"AI_PROVIDER non supportato: {configured}")
        for prefix in ("openrouter/", "groq/", "cerebras/", "mistral/"):
            if model.startswith(prefix) and prefix[:-1] != configured:
                raise RuntimeError("AI_PROVIDER non corrisponde al prefisso di AI_MODEL")
        return adapter
    for prefix in ("openrouter/", "groq/", "cerebras/", "mistral/"):
        if model.startswith(prefix):
            return PROVIDERS[prefix[:-1]]
    # Historical unprefixed OpenRouter model IDs remain supported.
    return PROVIDERS["openrouter"]
