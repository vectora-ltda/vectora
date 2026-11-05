"""Tools de mídia — geração de imagem e voz pelo provider ativo.

Invariante central coberto aqui: a tool **nunca** troca de provider por
conta própria. Se o modelo escolhido não gera imagem, o certo é avisar —
gerar em outro lugar chamaria (e cobraria) uma API que o usuário não pediu.
Cada caminho feliz tem o par de erro/borda no mesmo teste.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import uuid4

import pytest

from backend.services.media_quota import media_quota
from backend.settings import provider_supports
from backend.tools import media
from backend.tools.context import ToolContext


def _ctx(model: str) -> ToolContext:
    return ToolContext(model=model, thread_id="t-media", tool_call_id=uuid4().hex)


def _quota_ctx(model: str) -> ToolContext:
    return ToolContext(
        model=model,
        thread_id="t-media-quota",
        user_id="media-quota-user",
        tool_call_id="quota-call",
    )


@pytest.mark.asyncio
async def test_byok_nao_reserva_quota_nem_altera_uso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chave BYOK confirmada isenta a reserva, sem mudar o contrato da tool."""
    ctx = _quota_ctx("openai:gpt-5")
    ctx._extra["media_billing_source"] = "byok"
    called = False
    summary_called = False

    async def _reserve(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return object()

    async def _summary(_user_id: str) -> dict[str, int | str]:
        nonlocal summary_called
        summary_called = True
        return {"remaining": 10, "limit": 10, "period": "2026-09", "used": 0}

    monkeypatch.setattr(media_quota, "reserve", _reserve)
    monkeypatch.setattr(media_quota, "summary", _summary)
    result, error = await media._reserve_media(ctx, "generate_image")

    assert result is None
    assert error is None
    assert called is False
    assert summary_called is False


@pytest.mark.asyncio
async def test_credencial_ausente_nao_recebe_isencao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem marcador confiável, a tool segue o fluxo normal de quota."""
    ctx = _quota_ctx("openai:gpt-5")
    called = False

    async def _reserve(**_kwargs: object) -> object:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(media_quota, "reserve", _reserve)
    monkeypatch.setattr(
        media_quota,
        "summary",
        lambda _user_id: _summary_zero(),
    )
    result, error = await media._reserve_media(ctx, "generate_image")

    assert called is True
    assert result is None
    assert error is not None


async def _summary_zero() -> dict[str, int | str]:
    return {"remaining": 0, "limit": 10, "period": "2026-09", "used": 0}


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        (
            "generate_image",
            lambda: media.generate_image(
                ctx=_quota_ctx("openai:gpt-5"), prompt="segredo"
            ),
        ),
        (
            "text_to_speech",
            lambda: media.text_to_speech(
                ctx=_quota_ctx("openai:gpt-5"), text="segredo"
            ),
        ),
        (
            "generate_video",
            lambda: media.generate_video(
                ctx=_quota_ctx("google-genai:veo-3"), prompt="segredo"
            ),
        ),
    ],
)
async def test_quota_esgotada_devolve_resumo_sem_chamar_provider(
    operation: str,
    call: Callable[[], Coroutine[Any, Any, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O bloqueio de quota informa saldo e renovação sem enviar o payload."""

    async def reject_reservation(**_kwargs: object) -> None:
        return None

    async def quota_summary(_user_id: str) -> dict[str, int | str]:
        return {"remaining": 0, "limit": 10, "period": "2026-09", "used": 10}

    async def provider_called(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError(f"provider chamado para {operation}")

    monkeypatch.setattr(media_quota, "reserve", reject_reservation)
    monkeypatch.setattr(media_quota, "summary", quota_summary)
    monkeypatch.setattr(media, "_generate_image_bytes", provider_called)
    monkeypatch.setattr(media, "_synthesize_speech_bytes", provider_called)
    monkeypatch.setattr(media, "_generate_video_bytes", provider_called)

    result = json.loads(await call())

    assert result["error"] == "quota mensal de mídia esgotada"
    assert result["operation"] == operation
    assert result["remaining"] == 0
    assert result["limit"] == 10
    assert result["period"] == "2026-09"
    assert result["next_step"] == "aguarde a renovação do período ou atualize seu plano"


# ---------------------------------------------------------------------------
# provider_supports — matriz de capacidades
# ---------------------------------------------------------------------------


def test_provider_supports_resolve_capacidade_por_provider_fixo():
    # Happy: providers que fazem imagem/voz de verdade.
    assert provider_supports("openai", "image") is True
    assert provider_supports("google-genai", "tts") is True
    assert provider_supports("cohere", "reranker") is True

    # Erro/borda: provider que NÃO faz — precisa dar False, senão a tool
    # tentaria chamar um SDK que não tem esse endpoint.
    assert provider_supports("anthropic", "image") is False
    assert provider_supports("cohere", "image") is False
    assert provider_supports("provider-que-nao-existe", "image") is False


def test_provider_supports_gateway_depende_do_modelo_configurado(monkeypatch):
    from backend.settings import settings

    # Erro/borda primeiro: sem modelo configurado a capacidade não existe.
    monkeypatch.setattr(settings, "ollama_image_model", None, raising=False)
    assert provider_supports("ollama", "image") is False

    # Happy: com modelo escolhido pelo usuário, passa a existir.
    monkeypatch.setattr(settings, "ollama_image_model", "algum-modelo", raising=False)
    assert provider_supports("ollama", "image") is True

    # Ollama STT remains unavailable until a real transcription adapter exists;
    # a configured model must not advertise an unexecutable capability.
    assert provider_supports("ollama", "stt") is False

    # Borda: string vazia conta como não configurado (não como "existe").
    monkeypatch.setattr(settings, "openrouter_tts_model", "", raising=False)
    assert provider_supports("openrouter", "tts") is False


# ---------------------------------------------------------------------------
# generate_image
# ---------------------------------------------------------------------------


async def test_generate_image_provider_sem_suporte_avisa_e_nao_chama_sdk(monkeypatch):
    chamou = {"sdk": False}

    def _nunca(*_a, **_k):
        chamou["sdk"] = True
        raise AssertionError("SDK não deveria ser chamado")

    monkeypatch.setattr(media, "_generate_image_bytes", _nunca)

    out = json.loads(
        await media.generate_image(ctx=_ctx("cohere:command-r"), prompt="um gato")
    )

    assert "error" in out
    assert "não suporta" in out["error"]
    # O ponto do teste: falhou ANTES de tocar em qualquer SDK — nunca
    # tentou gerar em outro provider pra "resolver" sozinho.
    assert chamou["sdk"] is False


async def test_generate_image_happy_path_persiste_arquivo(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")
    monkeypatch.setattr(media, "_generate_image_bytes", lambda *_a: b"\x89PNG-fake")

    out = json.loads(
        await media.generate_image(ctx=_ctx("openai:gpt-5"), prompt="um gato")
    )

    assert "error" not in out
    assert out["provider"] == "openai"
    assert out["bytes"] == len(b"\x89PNG-fake")
    from pathlib import Path

    assert Path(out["path"]).read_bytes() == b"\x89PNG-fake"
    # `url` servível — sem isso, `<img src>` no chat não tem o que carregar
    # (só o `path` de arquivo no servidor, inútil no browser).
    assert out["url"] == f"/artifacts/t-media/media/{Path(out['path']).name}"


def test_generate_image_render_hint_bate_com_chave_reconhecida_pelo_frontend():
    """`render_hint="image"` (valor antigo) não bate com nenhuma chave do
    `RENDERERS` do frontend (`tool-call-renderer.tsx`) — só `"image_preview"`
    e `"browser_screenshot"` disparam `ImagePreview`. Com o valor errado, a
    imagem gerada sempre caía no fallback `RENDERERS.json`, mesmo com `url`
    servível — reproduzido lendo os dois arquivos lado a lado, não em
    browser real (não é testável em Python, mas o contrato É)."""
    from backend.tools.registry import TOOL_REGISTRY

    spec = TOOL_REGISTRY.get("generate_image")
    assert spec is not None
    assert spec.extras.render_hint == "image_preview"


async def test_generate_image_prompt_vazio_e_sdk_quebrado_viram_erro_tipado(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    # Erro/borda 1: prompt vazio nunca chega no SDK.
    vazio = json.loads(
        await media.generate_image(ctx=_ctx("openai:gpt-5"), prompt="   ")
    )
    assert "error" in vazio

    # Erro/borda 2: SDK explodindo vira string de erro, não exceção
    # propagada (falha de tool não derruba o grafo).
    def _explode(*_a):
        raise RuntimeError("cota estourada")

    monkeypatch.setattr(media, "_generate_image_bytes", _explode)
    quebrado = json.loads(
        await media.generate_image(ctx=_ctx("openai:gpt-5"), prompt="um gato")
    )
    assert "error" in quebrado
    assert "cota estourada" in quebrado["error"]

    # Erro/borda 3: provider devolvendo vazio não grava arquivo de 0 byte.
    monkeypatch.setattr(media, "_generate_image_bytes", lambda *_a: b"")
    vazio_sdk = json.loads(
        await media.generate_image(ctx=_ctx("openai:gpt-5"), prompt="um gato")
    )
    assert "error" in vazio_sdk


# ---------------------------------------------------------------------------
# text_to_speech
# ---------------------------------------------------------------------------


async def test_text_to_speech_sem_suporte_avisa_com_suporte_persiste(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    # Erro/borda: Anthropic não sintetiza voz.
    negado = json.loads(
        await media.text_to_speech(ctx=_ctx("anthropic:claude"), text="olá")
    )
    assert "error" in negado

    # Happy: OpenAI sintetiza.
    monkeypatch.setattr(media, "_synthesize_speech_bytes", lambda *_a: b"ID3-fake")
    ok = json.loads(await media.text_to_speech(ctx=_ctx("openai:gpt-5"), text="olá"))
    assert "error" not in ok
    assert ok["bytes"] == len(b"ID3-fake")


async def test_text_to_speech_texto_vazio_e_audio_vazio_sao_rejeitados(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    vazio = json.loads(await media.text_to_speech(ctx=_ctx("openai:gpt-5"), text="  "))
    assert "error" in vazio

    monkeypatch.setattr(media, "_synthesize_speech_bytes", lambda *_a: b"")
    sem_audio = json.loads(
        await media.text_to_speech(ctx=_ctx("openai:gpt-5"), text="olá")
    )
    assert "error" in sem_audio


# ---------------------------------------------------------------------------
# Resolução de provider
# ---------------------------------------------------------------------------


def test_provider_vem_do_config_da_sessao_nao_do_global(monkeypatch):
    """A sessão pode ter trocado de modelo em runtime — vale o que está no
    ctx daquela conversa, não a preferência global."""

    class _Global:
        active_provider = "cohere"

    monkeypatch.setattr(
        "backend.workspace.runtime_settings.runtime_settings", _Global()
    )

    # Happy: ctx manda.
    assert media._active_provider(_ctx("openai:gpt-5")) == "openai"

    # Erro/borda: sem modelo no ctx cai no global — nunca retorna vazio
    # silenciosamente quando há um provider ativo.
    assert media._active_provider(ToolContext()) == "cohere"
    assert media._active_provider(_ctx("")) == "cohere"


@pytest.mark.parametrize("capability", ["image", "tts"])
async def test_nenhuma_tool_de_midia_troca_de_provider_sozinha(capability, monkeypatch):
    """Mesmo com outro provider perfeitamente capaz configurado, a tool
    recusa em vez de desviar para ele."""
    from backend.settings import settings

    monkeypatch.setattr(settings, "ollama_image_model", "modelo-capaz", raising=False)
    monkeypatch.setattr(settings, "ollama_tts_model", "modelo-capaz", raising=False)

    fn = media.generate_image if capability == "image" else media.text_to_speech
    kwargs = {"prompt": "x"} if capability == "image" else {"text": "x"}

    out = json.loads(await fn(ctx=_ctx("anthropic:claude"), **kwargs))

    assert "error" in out
    # Recusou em vez de desviar: nenhum arquivo foi gerado e nenhum outro
    # provider aparece como tendo atendido a chamada. (A mensagem *cita*
    # Ollama como sugestão de configuração — isso é orientação pro usuário,
    # não sinal de que a tool gerou lá por conta própria.)
    assert "path" not in out
    assert out.get("provider") is None


# ---------------------------------------------------------------------------
# Modelo escolhido na UI vs. env var
# ---------------------------------------------------------------------------


def test_escolha_da_ui_vence_a_env_var(monkeypatch):
    """Sem essa precedência, quem configurou por env nunca conseguiria trocar
    de modelo pela interface: a env sempre ganharia e a UI pareceria não
    salvar."""
    from backend.settings import configured_gateway_model, settings
    from backend.workspace.runtime_settings import runtime_settings

    monkeypatch.setattr(settings, "ollama_image_model", "modelo-da-env", raising=False)

    # Só env configurada: é ela que vale.
    monkeypatch.setattr(
        type(runtime_settings), "media_settings", property(lambda _s: {})
    )
    assert configured_gateway_model("ollama", "image") == "modelo-da-env"

    # Happy: usuário escolheu na UI -> a escolha vence.
    monkeypatch.setattr(
        type(runtime_settings),
        "media_settings",
        property(lambda _s: {"ollama_image_model": "modelo-da-ui"}),
    )
    assert configured_gateway_model("ollama", "image") == "modelo-da-ui"

    # Erro/borda: escolha vazia na UI **não** mascara a env — limpar o campo
    # devolve o controle pra env var em vez de desligar a capacidade.
    monkeypatch.setattr(
        type(runtime_settings),
        "media_settings",
        property(lambda _s: {"ollama_image_model": "   "}),
    )
    assert configured_gateway_model("ollama", "image") == "modelo-da-env"

    # Erro/borda: sem nenhum dos dois, a capacidade não existe.
    monkeypatch.setattr(settings, "ollama_image_model", None, raising=False)
    monkeypatch.setattr(
        type(runtime_settings), "media_settings", property(lambda _s: {})
    )
    assert configured_gateway_model("ollama", "image") == ""
    from backend.settings import provider_supports

    assert provider_supports("ollama", "image") is False


def test_runtime_settings_indisponivel_cai_na_env_sem_estourar(monkeypatch):
    """Erro/borda: uma checagem de capacidade não pode explodir só porque o
    runtime_settings ainda não subiu (boot muito cedo, teste isolado)."""
    import backend.settings as settings_mod
    from backend.settings import settings

    monkeypatch.setattr(settings, "openrouter_tts_model", "voz-da-env", raising=False)

    class _Boom:
        @property
        def media_settings(self):
            raise RuntimeError("runtime_settings não inicializado")

    monkeypatch.setattr("backend.workspace.runtime_settings.runtime_settings", _Boom())
    assert settings_mod.configured_gateway_model("openrouter", "tts") == "voz-da-env"


def test_reranker_type_recusa_provider_sem_api_de_rerank():
    """Só entra provider com endpoint de rerank de verdade.

    O OpenRouter tem: ``POST /api/v1/rerank`` (`model`, `query`, `documents`,
    `top_n`), por isso é aceito. O Ollama não tem esse endpoint e continua
    rejeitado.
    """
    import pydantic

    from backend.settings import Settings

    # Happy: os providers que de fato têm API de rerank.
    assert Settings(reranker_type="cohere").reranker_type == "cohere"
    assert Settings(reranker_type="voyage").reranker_type == "voyage"
    assert Settings(reranker_type="openrouter").reranker_type == "openrouter"

    # Erro/borda: provider sem rerank é rejeitado na validação, não ignorado
    # silenciosamente lá na frente em `_build_reranker`.
    for invalido in ("ollama", "qualquer-coisa"):
        with pytest.raises(pydantic.ValidationError):
            Settings(reranker_type=invalido)


class TestOpenRouterLigadoNasTools:
    """Com OpenRouter ativo e configurado, `generate_image`/`text_to_speech`
    chamam o cliente nativo em vez de levantar `NotImplementedError`."""

    @staticmethod
    def _sem_key(monkeypatch) -> None:
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "openrouter_api_key", "", raising=False)

    def test_imagem_via_openrouter_chama_o_cliente_nativo(self, monkeypatch):
        from backend.settings import settings as _s
        from backend.tools import media

        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)
        monkeypatch.setattr(
            "backend.settings.configured_gateway_model",
            lambda _p, _c: "openai/gpt-image-1",
        )

        async def _fake(_client, *, model, prompt, **_kw):
            assert model == "openai/gpt-image-1"
            assert prompt == "um gato"
            return b"\x89PNG-fake"

        monkeypatch.setattr("backend.llm.openrouter.media.generate_image_bytes", _fake)

        assert media._generate_image_bytes("openrouter", "um gato") == b"\x89PNG-fake"

    def test_tts_via_openrouter_chama_o_cliente_nativo(self, monkeypatch):
        from backend.settings import settings as _s
        from backend.tools import media

        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)
        monkeypatch.setattr(
            "backend.settings.configured_gateway_model",
            lambda _p, _c: "openai/gpt-4o-mini-tts",
        )

        async def _fake(_client, *, model, text, voice, **_kw):
            assert voice == "alloy"
            return b"audio-cru"

        monkeypatch.setattr(
            "backend.llm.openrouter.media.synthesize_speech_bytes", _fake
        )

        assert (
            media._synthesize_speech_bytes("openrouter", "oi", "alloy") == b"audio-cru"
        )

    def test_provider_ainda_sem_cliente_segue_levantando(self, monkeypatch):
        """Erro/borda: o Ollama continua sem cliente de imagem. Ligar o
        OpenRouter não pode ter transformado o erro claro num silêncio."""
        from backend.tools import media

        monkeypatch.setattr(
            "backend.settings.configured_gateway_model", lambda _p, _c: "algum-modelo"
        )

        with pytest.raises(NotImplementedError, match="ollama"):
            media._generate_image_bytes("ollama", "x")
