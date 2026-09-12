"""Geração de mídia (imagem, voz) pelo provider que o usuário já escolheu.

Regra central: **nunca trocar de provider por conta própria**. Se o modelo
selecionado não gera imagem, a tool devolve um erro legível e o agente
avisa — gerar em outro provider chamaria uma API que o usuário não pediu
(e cobraria por ela). Mesmo princípio que `chat.py` já aplica pra visão
(`VISION_CAPABLE_PROVIDERS`).

O binário sai como arquivo em ``~/.vectora/artifacts/{session_id}/media/``
— mesma raiz de `create_artifact`, mas em subpasta própria: artifact é
markdown versionado, mídia é binário imutável (regerar produz um arquivo
novo, não uma versão do anterior).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.services.media_quota import QuotaReservation, QuotaState


def _session_id(ctx: ToolContext) -> str:
    """thread_id do contexto — nunca do texto do prompt (mesma razão de
    `fs.py::_session_id_from_config`: o system prompt é cacheado por
    workspace, o thread real não aparece nele)."""
    return ctx.thread_id


def _active_provider(ctx: ToolContext) -> str:
    """Provider do modelo ativo da sessão.

    Lê de `ctx.model` primeiro (a sessão pode ter trocado de modelo em
    runtime) e só cai no runtime_settings quando o contexto não traz —
    assim a tool respeita a escolha feita naquela conversa, não a global.
    """
    if ":" in ctx.model:
        return ctx.model.split(":", 1)[0]
    try:
        from backend.workspace.runtime_settings import runtime_settings

        return runtime_settings.active_provider
    except Exception:
        return ""


def _active_model(ctx: ToolContext) -> str:
    """Nome do modelo ativo, sem o prefixo de provider.

    O SDK espera `gemini-2.5-flash`, não `google-genai:gemini-2.5-flash` —
    mandar o spec inteiro vira 404 de modelo inexistente.
    """
    return ctx.model.split(":", 1)[1] if ":" in ctx.model else ctx.model


async def _reserve_media(
    ctx: ToolContext, operation: str
) -> tuple[QuotaReservation | None, str | None]:
    """Reserva quota antes de tocar um provider gerenciado.

    Credenciais BYOK são marcadas pelo contexto autenticado do backend; elas
    não consomem a quota interna. O texto do prompt e argumentos da tool nunca
    podem escolher essa origem.
    """
    if getattr(ctx, "_extra", {}).get("media_billing_source") == "byok":
        return None, None
    from backend.persistence.telemetry import telemetry
    from backend.services.media_quota import (
        media_estimate_record,
        media_quota,
        new_idempotency_key,
    )

    # A cobrança precisa estar vinculada a uma identidade estável do ciclo de
    # tool. Sem ela não há como distinguir retry de uma nova operação; falhar
    # fechado evita uma segunda cobrança acidental.
    stable_call_id = ctx.tool_call_id
    if not stable_call_id:
        return None, json.dumps(
            {
                "error": "identidade estável ausente para operação gerenciada",
                "operation": operation,
            },
            ensure_ascii=False,
        )
    try:
        idempotency_key = new_idempotency_key(stable_call_id, operation)
    except ValueError:
        return None, json.dumps(
            {
                "error": "identidade estável ausente para operação gerenciada",
                "operation": operation,
            },
            ensure_ascii=False,
        )
    provider = _active_provider(ctx)
    model = _active_model(ctx)
    estimate = media_estimate_record(operation, provider=provider, model=model)
    telemetry.record_media_quota(
        "estimate",
        operation=estimate.operation,
        provider=estimate.provider,
        model=estimate.model,
        estimate_version=estimate.version,
        billable_unit=estimate.billable_unit,
        currency=estimate.currency,
        units=estimate.units,
        idempotency_key=idempotency_key,
    )
    reservation = await media_quota.reserve(
        user_id=ctx.user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        units=estimate.units,
    )
    if reservation is None:
        quota_summary = await media_quota.summary(ctx.user_id)
        telemetry.record_media_quota(
            "blocked",
            operation=operation,
            provider=provider,
            model=model,
            result="quota_exceeded",
            idempotency_key=idempotency_key,
        )
        return None, json.dumps(
            {
                "error": "quota mensal de mídia esgotada",
                "operation": operation,
                "remaining": int(quota_summary.get("remaining", 0)),
                "limit": int(quota_summary.get("limit", 0)),
                "period": str(quota_summary.get("period", "")),
                "next_step": "aguarde a renovação do período ou atualize seu plano",
            },
            ensure_ascii=False,
        )
    if reservation.state in {"finalized", "unknown"}:
        return None, json.dumps(
            {
                "error": "operação já concluída ou em estado incerto; não será repetida",
                "operation": operation,
            },
            ensure_ascii=False,
        )
    telemetry.record_media_quota(
        "reserved",
        operation=operation,
        provider=provider,
        model=model,
        units=reservation.units,
        state=reservation.state,
        idempotency_key=idempotency_key,
    )
    return reservation, None


async def _finalize_media(
    reservation: QuotaReservation | None, state: QuotaState
) -> None:
    if reservation is None:
        return
    from backend.persistence.telemetry import telemetry
    from backend.services.media_quota import media_quota

    await media_quota.finalize(reservation, state=state)
    telemetry.record_media_quota(
        "finalized",
        operation=reservation.operation,
        units=reservation.units,
        state=state,
        idempotency_key=reservation.id,
    )


def _media_dir(session_id: str) -> Path:
    # `settings.vectora_home` (não `Path.home()` direto) — respeita
    # `VECTORA_HOME` e é o mesmo diretório que
    # `api/handlers/artifacts.py::_artifacts_dir` usa pra servir o binário
    # de volta; hardcoded diferente quebraria a URL servível em qualquer
    # instalação com `VECTORA_HOME` customizado.
    return settings.vectora_home / "artifacts" / (session_id or "sem-sessao") / "media"


def _validated_video_path(ctx: ToolContext, path: str) -> Path | None:
    """Resolve video input inside the current session media directory only."""
    if not ctx.thread_id:
        return None
    root = _media_dir(ctx.thread_id).resolve()
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if resolved.is_symlink() or not resolved.is_file():
        return None
    return resolved


def _media_url(session_id: str, path: Path) -> str:
    """URL relativa e servível (`GET /artifacts/{session_id}/media/
    {filename}`, `api/handlers/artifacts.py::get_media_artifact`) pro
    binário que acabou de ser persistido — sem isso, `generate_image`/
    `text_to_speech`/`generate_video` devolviam só um `path` de arquivo NO
    SERVIDOR, que o `<img src>`/`<audio src>` do chat não consegue
    carregar (contrato que `ImagePreview`, `tool-call-renderer.tsx`, já
    espera: `url`/`src`/`image_url`)."""
    return f"/artifacts/{session_id or 'sem-sessao'}/media/{path.name}"


def _unsupported(provider: str, capability: str, hint: str) -> str:
    """Erro legível pro LLM relaiar — nunca uma exceção crua."""
    return json.dumps(
        {
            "error": (
                f"o provider ativo ({provider or 'nenhum'}) não suporta "
                f"{capability}. {hint} Não troque de provider por conta "
                "própria — avise o usuário e deixe ele escolher."
            )
        },
        ensure_ascii=False,
    )


def _persist(session_id: str, data: bytes, suffix: str) -> Path:
    directory = _media_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}{suffix}"
    path.write_bytes(data)
    return path


def _generate_image_bytes(provider: str, prompt: str) -> bytes:
    """Chama o SDK do provider ativo. Cada provider expõe geração de imagem
    de um jeito diferente — o mapeamento fica aqui, isolado da tool."""
    from backend.settings import configured_gateway_model, settings

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        result = client.images.generate(model="gpt-image-1", prompt=prompt, n=1)
        # O SDK tipa `data` como opcional: resposta sem imagem é possível
        # (filtro de conteúdo, por exemplo) e vira erro claro no caller.
        entries = result.data or []
        b64 = entries[0].b64_json if entries else None
        return base64.b64decode(b64) if b64 else b""

    if provider == "google-genai":
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        result = client.models.generate_images(
            model="imagen-4.0-generate-001", prompt=prompt
        )
        images = result.generated_images or []
        if not images:
            return b""
        image = images[0].image
        raw = getattr(image, "image_bytes", None) if image else None
        return bytes(raw) if raw else b""

    # Ollama/OpenRouter: modelo escolhido pelo usuário (UI vence env — ver
    # `configured_gateway_model`).
    model = configured_gateway_model(provider, "image")

    if provider == "openrouter":
        import asyncio

        from backend.llm.openrouter.client import OpenRouterClient
        from backend.llm.openrouter.media import generate_image_bytes

        client = OpenRouterClient(api_key=settings.openrouter_api_key or "")
        return asyncio.run(generate_image_bytes(client, model=model, prompt=prompt))

    raise NotImplementedError(
        f"geração de imagem via {provider} (modelo {model}) ainda não tem "
        "cliente implementado"
    )


def _synthesize_speech_bytes(provider: str, text: str, voice: str) -> bytes:
    from backend.settings import configured_gateway_model, settings

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.audio.speech.create(
            model="gpt-4o-mini-tts", voice=voice or "alloy", input=text
        )
        return response.read()

    if provider == "google-genai":
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        result = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts", contents=text
        )
        # Toda a cadeia é opcional no SDK — resposta bloqueada/vazia é um
        # caminho real, não um "não deveria acontecer".
        candidates = result.candidates or []
        content = candidates[0].content if candidates else None
        parts = (content.parts if content else None) or []
        inline = parts[0].inline_data if parts else None
        raw = inline.data if inline else None
        return bytes(raw) if raw else b""

    model = configured_gateway_model(provider, "tts")

    if provider == "openrouter":
        import asyncio

        from backend.llm.openrouter.client import OpenRouterClient
        from backend.llm.openrouter.media import synthesize_speech_bytes

        client = OpenRouterClient(api_key=settings.openrouter_api_key or "")
        return asyncio.run(
            synthesize_speech_bytes(
                client, model=model, text=text, voice=voice or "alloy"
            )
        )

    raise NotImplementedError(
        f"síntese de voz via {provider} (modelo {model}) ainda não tem "
        "cliente implementado"
    )


@vtool(
    extras=ToolExtras(
        # `"image_preview"` — a CHAVE que `RENDERERS` (frontend/components/
        # chat/tool-call-renderer.tsx) de fato reconhece. `"image"` (valor
        # antigo) não bate com nenhuma chave do mapa, então SEMPRE caía no
        # fallback `RENDERERS.json` — a imagem gerada nunca apareceu inline
        # no chat, mesmo depois do `url` virar servível.
        render_hint="image_preview",
        category="media",
        destructive=False,
        icon="image",
    )
)
async def generate_image(ctx: ToolContext, prompt: str) -> str:
    """Gera uma imagem a partir de uma descrição, usando o modelo ativo.

    Só funciona se o provider selecionado gera imagem (Gemini e OpenAI
    geram; Anthropic e Cohere não). Com Ollama/OpenRouter, depende de o
    usuário ter configurado um modelo de imagem nas Settings.

    Se o provider ativo não suporta, esta tool devolve um erro explicando —
    relaie a mensagem ao usuário e NÃO tente gerar com outro provider.

    Args:
        prompt: Descrição do que desenhar, em linguagem natural.

    Returns:
        JSON com o path do arquivo gerado, ou com `error` se o provider
        ativo não gera imagem.
    """
    provider = _active_provider(ctx)
    reservation = None
    submitted = False
    try:
        from backend.settings import configured_gateway_model, provider_supports

        if not provider_supports(provider, "image"):
            return _unsupported(
                provider,
                "geração de imagem",
                "Troque para um modelo Gemini/OpenAI, ou configure um "
                "modelo de imagem em Settings (Ollama/OpenRouter).",
            )
        if not prompt.strip():
            return json.dumps({"error": "prompt vazio — descreva a imagem"})

        reservation, quota_error = await _reserve_media(ctx, "generate_image")
        if quota_error:
            return quota_error

        submitted = True
        data = await asyncio.to_thread(_generate_image_bytes, provider, prompt)
        if not data:
            await _finalize_media(reservation, "unknown")
            return json.dumps({"error": "provider devolveu imagem vazia"})
        session_id = _session_id(ctx)
        path = await asyncio.to_thread(_persist, session_id, data, ".png")
        logger.info("generate_image: %s bytes → %s", len(data), path)
        await _finalize_media(reservation, "finalized")
        return json.dumps(
            {
                "path": str(path),
                "url": _media_url(session_id, path),
                "provider": provider,
                "bytes": len(data),
            },
            ensure_ascii=False,
        )
    except asyncio.CancelledError:
        await _finalize_media(reservation, "unknown" if submitted else "cancelled")
        raise
    except Exception as exc:
        await _finalize_media(
              reservation,
              "unknown"
              if submitted and not isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError, ValueError))
              else "failed",
          )
        logger.exception("generate_image: falha", extra={"provider": provider})
        return json.dumps(
            {"error": f"falha ao gerar imagem: {exc}"}, ensure_ascii=False
        )


@vtool(
    extras=ToolExtras(
        # `"audio"` não existe em `RenderHint` (frontend/lib/types/render.ts)
        # — sempre caiu no fallback JSON cru. Sem player de áudio inline
        # ainda (fora de escopo desta sprint), `"artifact"` dá um link de
        # download real via `ArtifactCard`, em vez de um path de servidor
        # inútil que era o comportamento de antes.
        render_hint="artifact",
        category="media",
        destructive=False,
        icon="volume-2",
    )
)
async def text_to_speech(ctx: ToolContext, text: str, voice: str = "") -> str:
    """Converte texto em áudio falado, usando o modelo ativo.

    Mesma regra de `generate_image`: se o provider ativo não faz síntese de
    voz, devolve erro explicando — não troque de provider sozinho.

    Args:
        text: Texto a ser falado.
        voice: Nome da voz (opcional; cada provider tem as suas).

    Returns:
        JSON com o path do áudio gerado, ou com `error`.
    """
    provider = _active_provider(ctx)
    reservation = None
    submitted = False
    try:
        from backend.settings import configured_gateway_model, provider_supports

        if not provider_supports(provider, "tts"):
            return _unsupported(
                provider,
                "síntese de voz",
                "Troque para um modelo Gemini/OpenAI, ou configure um "
                "modelo de voz em Settings (Ollama/OpenRouter).",
            )
        if not text.strip():
            return json.dumps({"error": "texto vazio — nada a falar"})

        reservation, quota_error = await _reserve_media(ctx, "text_to_speech")
        if quota_error:
            return quota_error

        submitted = True
        data = await asyncio.to_thread(_synthesize_speech_bytes, provider, text, voice)
        if not data:
            await _finalize_media(reservation, "unknown")
            return json.dumps({"error": "provider devolveu áudio vazio"})
        session_id = _session_id(ctx)
        path = await asyncio.to_thread(_persist, session_id, data, ".mp3")
        logger.info("text_to_speech: %s bytes → %s", len(data), path)
        await _finalize_media(reservation, "finalized")
        return json.dumps(
            {
                "title": path.name,
                "artifact_type": "audio",
                "path": str(path),
                "url": _media_url(session_id, path),
                "provider": provider,
                "bytes": len(data),
            },
            ensure_ascii=False,
        )
    except asyncio.CancelledError:
        await _finalize_media(reservation, "unknown" if submitted else "cancelled")
        raise
    except Exception as exc:
        await _finalize_media(
              reservation,
              "unknown"
              if submitted and not isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError, ValueError))
              else "failed",
          )
        logger.exception("text_to_speech: falha", extra={"provider": provider})
        return json.dumps({"error": f"falha ao gerar áudio: {exc}"}, ensure_ascii=False)


class VideoGenerationTimeoutError(RuntimeError):
    """O job não chegou a um estado terminal dentro do teto de tempo.

    Separado de "falhou": o job pode seguir rodando (e sendo cobrado) no
    provider. Quem trata precisa dizer isso, senão o usuário regera achando
    que não saiu nada.
    """


#: Geração de vídeo leva minutos. Os dois números existem para o teste poder
#: zerá-los; em produção o teto é generoso mas nunca infinito.
_VIDEO_POLL_INTERVAL_S = 10.0
_VIDEO_TIMEOUT_S = 900.0

#: Veo é o caminho de geração do Gemini. Fixo aqui, não configurável: o
#: `{provider}_video_model` das Settings é dos gateways (Ollama/OpenRouter),
#: onde o modelo é escolha do usuário. O nome vem de `models.list()` da API —
#: só as variantes `preview` expõem `predictLongRunning` hoje.
_GEMINI_VIDEO_MODEL = "veo-3.1-generate-preview"


async def _gemini_video_bytes(
    client: Any,
    *,
    model: str,
    prompt: str,
    poll_interval_s: float = _VIDEO_POLL_INTERVAL_S,
    timeout_s: float = _VIDEO_TIMEOUT_S,
) -> bytes:
    """Dispara o Veo e acompanha a operação até concluir, com teto de tempo.

    O cliente entra por parâmetro para o polling ser testável sem SDK real —
    é a parte que precisa de teste, não a construção do client.
    """
    import asyncio
    import time

    operacao = await client.aio.models.generate_videos(model=model, prompt=prompt)
    limite = time.monotonic() + timeout_s

    while not getattr(operacao, "done", False):
        if time.monotonic() >= limite:
            msg = (
                f"geração de vídeo não concluiu em {timeout_s:.0f}s — o job "
                "pode seguir rodando no provider"
            )
            raise VideoGenerationTimeoutError(msg)
        if poll_interval_s:
            await asyncio.sleep(poll_interval_s)
        operacao = await client.aio.operations.get(operacao)

    resposta = getattr(operacao, "response", None)
    videos = getattr(resposta, "generated_videos", None) or []
    if not videos:
        return b""
    video = videos[0].video
    raw = getattr(video, "video_bytes", None)
    if raw is None:
        # Vídeo grande vem só como referência de arquivo — baixar é um passo
        # separado no SDK, e sem ele o retorno seria vazio sem erro nenhum.
        await client.aio.files.download(file=video)
        raw = getattr(video, "video_bytes", None)
    return bytes(raw) if raw else b""


def _bytes_do_output_openrouter(output: Any) -> bytes:
    """Extrai o binário do `output` do job de vídeo.

    A referência não fixa um formato único: o item vem com `b64_json` ou com
    uma `url`. Tratar só um dos dois deixaria metade das respostas virando
    arquivo vazio.
    """
    import httpx

    itens = output if isinstance(output, list) else [output]
    for item in itens:
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            return base64.b64decode(str(item["b64_json"]))
        url = item.get("url") or item.get("video_url")
        if url:
            resposta = httpx.get(str(url), timeout=120.0, follow_redirects=True)
            resposta.raise_for_status()
            return resposta.content
    return b""


async def _generate_video_bytes(provider: str, prompt: str) -> bytes:
    from backend.settings import configured_gateway_model, settings

    if provider == "google-genai":
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        return await _gemini_video_bytes(
            client, model=_GEMINI_VIDEO_MODEL, prompt=prompt
        )

    model = configured_gateway_model(provider, "video")

    if provider == "openrouter":
        from backend.llm.openrouter.client import OpenRouterClient
        from backend.llm.openrouter.video import VideoTimeoutError, generate_video

        client = OpenRouterClient(api_key=settings.openrouter_api_key or "")
        try:
            estado = await generate_video(client, model=model, prompt=prompt)
        except VideoTimeoutError as exc:
            # Traduz para a exceção local: o caller trata teto de tempo de um
            # jeito só, independente de qual provider gerou.
            raise VideoGenerationTimeoutError(str(exc)) from exc
        return _bytes_do_output_openrouter(estado.get("output"))

    raise NotImplementedError(
        f"geração de vídeo via {provider} (modelo {model}) ainda não tem "
        "cliente implementado"
    )


async def _analyze_video_text(
    provider: str, model: str, path: str, question: str
) -> str:
    """Pergunta sobre o conteúdo de um vídeo já em disco."""
    from backend.settings import settings

    if provider == "google-genai":
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        arquivo = await client.aio.files.upload(file=path)
        resultado = await client.aio.models.generate_content(
            model=model or "gemini-2.5-flash", contents=[arquivo, question]
        )
        return str(getattr(resultado, "text", "") or "")

    raise NotImplementedError(f"análise de vídeo via {provider} não é suportada")


@vtool(
    extras=ToolExtras(
        render_hint="artifact",
        category="media",
        destructive=False,
        icon="video",
    )
)
async def generate_video(ctx: ToolContext, prompt: str) -> str:
    """Gera um vídeo curto a partir de uma descrição, usando o modelo ativo.

    Só o Gemini (Veo) gera vídeo nativamente; com Ollama/OpenRouter depende
    de o usuário ter configurado um modelo de vídeo nas Settings. Se o
    provider ativo não suporta, esta tool devolve um erro explicando —
    relaie ao usuário e NÃO tente com outro provider.

    A geração leva minutos e é acompanhada até terminar. Se estourar o teto
    de tempo, o resultado diz que o job pode seguir rodando no provider.

    Args:
        prompt: Descrição da cena, em linguagem natural.

    Returns:
        JSON com o path do vídeo gerado, ou com `error`.
    """
    provider = _active_provider(ctx)
    reservation = None
    submitted = False
    try:
        from backend.settings import provider_supports

        if not provider_supports(provider, "video"):
            return _unsupported(
                provider,
                "geração de vídeo",
                "Troque para um modelo Gemini (Veo), ou configure um modelo "
                "de vídeo em Settings (Ollama/OpenRouter).",
            )
        if not prompt.strip():
            return json.dumps({"error": "prompt vazio — descreva a cena"})

        reservation, quota_error = await _reserve_media(ctx, "generate_video")
        if quota_error:
            return quota_error

        submitted = True
        data = await _generate_video_bytes(provider, prompt)
        if not data:
            await _finalize_media(reservation, "unknown")
            return json.dumps({"error": "provider devolveu vídeo vazio"})
        session_id = _session_id(ctx)
        path = await asyncio.to_thread(_persist, session_id, data, ".mp4")
        logger.info("generate_video: %s bytes → %s", len(data), path)
        await _finalize_media(reservation, "finalized")
        return json.dumps(
            {
                "title": path.name,
                "artifact_type": "video",
                "path": str(path),
                "url": _media_url(session_id, path),
                "provider": provider,
                "bytes": len(data),
            },
            ensure_ascii=False,
        )
    except asyncio.CancelledError:
        await _finalize_media(reservation, "unknown" if submitted else "cancelled")
        raise
    except Exception as exc:
        await _finalize_media(
              reservation,
              "unknown"
              if submitted and not isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError, ValueError))
              else "failed",
          )
        logger.exception("generate_video: falha", extra={"provider": provider})
        return json.dumps({"error": f"falha ao gerar vídeo: {exc}"}, ensure_ascii=False)


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="media",
        destructive=False,
        icon="video",
    )
)
async def analyze_video(ctx: ToolContext, path: str, question: str) -> str:
    """Responde uma pergunta sobre o conteúdo de um vídeo em disco.

    Aceitar vídeo é mais raro que aceitar imagem: hoje só o Gemini lê vídeo
    como entrada. Se o provider ativo não lê, devolve erro explicando — não
    troque de provider sozinho.

    Args:
        path: Caminho do arquivo de vídeo.
        question: O que se quer saber sobre o vídeo.

    Returns:
        JSON com `answer`, ou com `error`.
    """
    provider = _active_provider(ctx)
    try:
        from backend.settings import VIDEO_INPUT_PROVIDERS

        if provider not in VIDEO_INPUT_PROVIDERS:
            return _unsupported(
                provider,
                "análise de vídeo",
                "Troque para um modelo Gemini — os outros providers leem "
                "imagem, mas não vídeo.",
            )
        safe_path = _validated_video_path(ctx, path)
        if safe_path is None:
            return json.dumps(
                {
                    "error": "vídeo deve estar na mídia da sessão atual e não pode ser symlink"
                },
                ensure_ascii=False,
            )
        if not question.strip():
            return json.dumps({"error": "pergunta vazia — diga o que quer saber"})

        model = _active_model(ctx)
        resposta = await _analyze_video_text(provider, model, str(safe_path), question)
        if not resposta.strip():
            return json.dumps({"error": "provider devolveu resposta vazia"})
        return json.dumps({"answer": resposta}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("analyze_video: falha", extra={"provider": provider})
        return json.dumps(
            {"error": f"falha ao analisar vídeo: {exc}"}, ensure_ascii=False
        )


def _read_audio_limited(path: Path, limit: int) -> bytes:
    with path.open("rb") as audio_file:
        return audio_file.read(limit + 1)


def _audio_signature_matches(data: bytes, suffix: str) -> bool:
    signatures = {
        ".wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        ".mp3": data.startswith(b"ID3")
        or (
            len(data) >= 2
            and data[0] == 0xFF
            and data[1] & 0xE0 == 0xE0
            and data[1] & 0x06 == 0x02
            and data[1] & 0x18 != 0x08
        ),
        ".m4a": len(data) >= 12 and data[4:8] == b"ftyp",
        ".webm": data.startswith(b"\x1a\x45\xdf\xa3"),
        ".ogg": data.startswith(b"OggS"),
    }
    return signatures.get(suffix, False)


@vtool(
    extras=ToolExtras(
        render_hint="text",
        category="media",
        destructive=False,
        icon="mic",
    )
)
async def audio_transcribe(ctx: ToolContext, path: str, language: str = "") -> str:
    """Transcreve um arquivo do workspace com o provider ativo."""
    provider = _active_provider(ctx)
    try:
        from backend.settings import configured_gateway_model, provider_supports
        from backend.tools.fs import _confine

        stt_model = configured_gateway_model(provider, "stt")
        if provider in {"openai", "openai-api"}:
            stt_model = "whisper-1"
        elif provider in {"google", "google-genai", "gemini"}:
            stt_model = "gemini-2.5-flash"
        if not stt_model or not provider_supports(provider, "stt"):
            return _unsupported(
                provider,
                "transcrição remota de áudio",
                "Troque para OpenAI/Gemini ou use transcribe_local sem enviar o áudio.",
            )
        resolved, error = _confine(path, ctx)
        if resolved is None:
            return json.dumps({"error": error}, ensure_ascii=False)
        if resolved.suffix.lower() not in {".wav", ".mp3", ".m4a", ".webm", ".ogg"}:
            return json.dumps({"error": "formato de áudio não suportado"})
        max_audio_bytes = 25 * 1024 * 1024
        data = await asyncio.to_thread(_read_audio_limited, resolved, max_audio_bytes)
        if len(data) > max_audio_bytes:
            return json.dumps({"error": "áudio excede o limite de 25 MB"})
        if not _audio_signature_matches(data, resolved.suffix.lower()):
            return json.dumps(
                {"error": "conteúdo de áudio incompatível com a extensão"}
            )
        mime = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
        }[resolved.suffix.lower()]
        from backend.llm.transcription import transcribe_audio

        text = await transcribe_audio(
            data,
            resolved.name,
            mime,
            provider=provider,
            model=stt_model,
            language=language,
        )
        if not text.strip():
            return json.dumps({"error": "provider devolveu transcrição vazia"})
        return json.dumps(
            {
                "text": text,
                "provider": provider,
                "model": stt_model,
                "language": language,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("audio_transcribe: falha", extra={"provider": provider})
        return json.dumps(
            {"error": "falha ao transcrever áudio; consulte os logs para detalhes"},
            ensure_ascii=False,
        )
