"""Informações de modelos LLM para o frontend (model selector).

Endpoint (exige auth via middleware):
    GET /models/providers — providers de LLM com credencial configurada +
    modelos dinâmicos registrados (provider routing — Ollama e OpenRouter).

O frontend usa isto para esconder do model selector os modelos cujo provider não
tem API key (ex.: sem chave OpenAI/Anthropic, GPT/Claude não aparecem) e para
mesclar os modelos Ollama/OpenRouter que o usuário registrou (backend/api/
handlers/provider_routing.py) no catálogo estático de deployment-config.ts.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/providers")
async def get_configured_providers() -> dict:
    """Providers de LLM com credencial configurada + modelos dinâmicos."""
    from backend.api.handlers.chat import _model_supports_vision
    from backend.api.handlers.provider_routing import (
        list_registered_llamacpp_models,
        list_registered_nine_router_models,
        list_registered_ollama_models,
        list_registered_openrouter_models,
        ollama_runtime_status,
    )
    from backend.settings import (
        AVAILABLE_MODELS,
        TOOL_CALLING_INCOMPATIBLE_MODELS,
        provider_capability_state,
        settings,
    )

    dynamic_models = []
    configured_providers = set(settings.configured_llm_providers())
    # O runtime gerenciado pode estar ativo mesmo sem uma URL persistida nas
    # settings (o wizard usa o endpoint local padrão). Nesse caso os modelos
    # registrados precisam continuar selecionáveis no seletor.
    ollama_ready = (await ollama_runtime_status()).reachable
    for provider, models in (
        ("ollama", await list_registered_ollama_models()),
        ("openrouter", await list_registered_openrouter_models()),
        ("nine_router", await list_registered_nine_router_models()),
        ("llamacpp", await list_registered_llamacpp_models()),
    ):
        for model in models:
            model_id = f"{provider}:{model.tag}"
            state = await _model_supports_vision(model_id)
            dynamic_models.append(
                {
                    "id": model_id,
                    "label": model.tag,
                    "provider": provider,
                    "available": provider in configured_providers
                    or (provider == "ollama" and ollama_ready),
                    "image_capability": state.value,
                }
            )
    # O DMR anuncia modelos por HTTP e não usa o registry SQLite da Vectora.
    # Incluí-los aqui mantém o seletor coerente com o catálogo local.
    if "dmr" in configured_providers:
        from backend.services.docker_model_runner import probe_dmr

        probe = await probe_dmr(settings.dmr_base_url)
        dmr = {"reachable": probe.reachable, "models": list(probe.models)}
        dmr_models = dmr["models"]
        # Keep the configured model selectable while Docker Model Runner is
        # temporarily unavailable (for example during Docker Desktop boot).
        # Readiness controls ``available``; it must not erase the user's
        # fallback configuration.
        configured_dmr_model = (settings.dmr_model or "").strip()
        if configured_dmr_model and configured_dmr_model not in dmr_models:
            dmr_models.append(configured_dmr_model)
        for tag in dmr_models if isinstance(dmr_models, list) else []:
            if isinstance(tag, str) and tag:
                model_id = f"dmr:{tag}"
                state = await _model_supports_vision(model_id)
                dynamic_models.append(
                    {
                        "id": model_id,
                        "label": tag,
                        "provider": "dmr",
                        "available": bool(dmr.get("reachable")),
                        "image_capability": state.value,
                    }
                )

    models = []
    for provider, provider_models in AVAILABLE_MODELS.items():
        available = provider in settings.configured_llm_providers()
        state = provider_capability_state(provider, "vision")
        for model in provider_models:
            models.append(
                {
                    "id": f"{provider}:{model}",
                    "label": model,
                    "provider": provider,
                    "available": available,
                    "image_capability": state.value,
                }
            )
    return {
        "providers": settings.configured_llm_providers(),
        "models": models,
        "dynamic_models": dynamic_models,
        "tool_incompatible_models": sorted(TOOL_CALLING_INCOMPATIBLE_MODELS),
    }
