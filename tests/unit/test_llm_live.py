"""``load_native_llm()`` (``backend/services/utils.py``) contra APIs reais —
Google Gemini e Cohere. Sem mock: cada teste faz pelo menos uma chamada de
rede de verdade e consome quota real das chaves configuradas em ``~/.vectora/.env``.

Guardado em duas camadas:
- marker ``live`` (``pyproject.toml``) — só roda via ``scons tests-live``,
  nunca em ``scons tests``.
- skip guard por provider via ``Settings.configured_llm_providers()`` — sem
  a credencial real configurada, o teste some da suíte com razão clara em
  vez de falhar.

O ChatClient nativo recebe ``tools=`` como parâmetro de chamada direto,
sem bind prévio.
"""

from __future__ import annotations

import pytest

from backend.services.utils import load_native_llm
from backend.settings import settings
from backend.vtypes.message import MessageRole, text_message

pytestmark = pytest.mark.live

requires_google = pytest.mark.skipif(
    "google-genai" not in settings.configured_llm_providers(),
    reason="GOOGLE_API_KEY não configurado em ~/.vectora/.env",
)
requires_cohere = pytest.mark.skipif(
    "cohere" not in settings.configured_llm_providers(),
    reason="COHERE_API_KEY não configurado em ~/.vectora/.env",
)

_GOOGLE_MODEL_ID = "google_genai:gemini-2.5-flash"
_COHERE_MODEL_ID = "cohere:command-a-03-2025"


# ---------------------------------------------------------------------------
# Completion simples (.agenerate)
# ---------------------------------------------------------------------------


@requires_google
async def test_completion_google_agenerate_real():
    model = load_native_llm(_GOOGLE_MODEL_ID)
    result = await model.agenerate(
        [text_message(MessageRole.USER, "Responda só com a palavra OK, nada mais.")]
    )
    assert result.text().strip()


@requires_cohere
async def test_completion_cohere_agenerate_real():
    model = load_native_llm(_COHERE_MODEL_ID)
    result = await model.agenerate(
        [text_message(MessageRole.USER, "Responda só com a palavra OK, nada mais.")]
    )
    assert result.text().strip()


@requires_google
async def test_completion_google_prompt_longo_real():
    model = load_native_llm(_GOOGLE_MODEL_ID)
    prompt = "Resuma em uma frase: " + "Vectora é um agente de codificação. " * 40
    result = await model.agenerate([text_message(MessageRole.USER, prompt)])
    assert result.text().strip()


# ---------------------------------------------------------------------------
# Streaming (.astream) — múltiplos chunks reais chegando token a token
# ---------------------------------------------------------------------------


@requires_google
async def test_streaming_google_astream_real():
    model = load_native_llm(_GOOGLE_MODEL_ID)
    chunks = [
        chunk.delta_text
        async for chunk in model.astream(
            [text_message(MessageRole.USER, "Conte de 1 a 5, um número por linha.")]
        )
        if chunk.delta_text
    ]
    assert len(chunks) > 1, "esperava múltiplos chunks reais do streaming"
    assert "".join(chunks).strip()


@requires_cohere
async def test_streaming_cohere_astream_real():
    model = load_native_llm(_COHERE_MODEL_ID)
    chunks = [
        chunk.delta_text
        async for chunk in model.astream(
            [text_message(MessageRole.USER, "Conte de 1 a 5, um número por linha.")]
        )
        if chunk.delta_text
    ]
    assert len(chunks) > 1, "esperava múltiplos chunks reais do streaming"
    assert "".join(chunks).strip()


# ---------------------------------------------------------------------------
# Erro claro em model_id inválido/inexistente
# ---------------------------------------------------------------------------


def test_load_native_llm_provider_desconhecido_levanta_erro_claro():
    with pytest.raises(ValueError, match="Provider de LLM nativo desconhecido"):
        load_native_llm("provider-que-nao-existe:algum-modelo")


@requires_google
async def test_completion_google_model_id_inexistente_real_levanta_erro():
    model = load_native_llm("google_genai:modelo-que-nao-existe-9999")
    with pytest.raises(Exception):  # noqa: B017 — exceção real do SDK
        await model.agenerate([text_message(MessageRole.USER, "oi")])


# ---------------------------------------------------------------------------
# Fallback Google → Cohere real (backend/llm/provider_fallback.py)
# ---------------------------------------------------------------------------


@requires_google
@requires_cohere
async def test_fallback_google_para_cohere_real(monkeypatch: pytest.MonkeyPatch):
    from backend.llm import provider_fallback

    monkeypatch.setenv("GOOGLE_API_KEY", "invalid-key-forced-for-live-test")
    monkeypatch.setattr(provider_fallback, "is_quota_error", lambda exc: True)
    monkeypatch.setattr(provider_fallback, "_fallback_order", list)

    async def _call(model_id: str):
        model = load_native_llm(model_id)
        return await model.agenerate(
            [text_message(MessageRole.USER, "Responda só com a palavra OK.")]
        )

    result = await provider_fallback.try_with_fallback(_call, _GOOGLE_MODEL_ID)
    assert result.text().strip()


@requires_google
async def test_fallback_sem_cohere_configurado_propaga_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    from backend.llm import provider_fallback

    monkeypatch.setenv("GOOGLE_API_KEY", "invalid-key-forced-for-live-test")
    monkeypatch.setattr(provider_fallback, "is_quota_error", lambda exc: True)
    monkeypatch.setattr(provider_fallback, "_fallback_order", list)
    monkeypatch.setattr(provider_fallback, "_provider_has_key", lambda provider: False)

    async def _call(model_id: str):
        model = load_native_llm(model_id)
        return await model.agenerate(
            [text_message(MessageRole.USER, "Responda só com a palavra OK.")]
        )

    with pytest.raises(provider_fallback.QuotaExhaustedError):
        await provider_fallback.try_with_fallback(_call, _GOOGLE_MODEL_ID)
