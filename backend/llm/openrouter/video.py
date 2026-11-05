"""Geração de vídeo do OpenRouter — ``POST /videos``, assíncrono.

Único endpoint do conjunto que não devolve o resultado na resposta: responde
HTTP 202 com ``id``/``polling_url``/``status`` e o ciclo é
``pending → in_progress → completed|failed|cancelled|expired``.

O polling tem **teto de tempo obrigatório**. A lição do incidente do NATS foi
exatamente essa: loop de espera sem corte gira para sempre quando o outro
lado nunca conclui, e o sintoma aparece como travamento, não como erro.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError

logger = logging.getLogger(__name__)

#: Estados terminais do ciclo documentado. `completed` é o único de sucesso.
_TERMINAIS_DE_FALHA = frozenset({"failed", "cancelled", "expired"})

_DEFAULT_POLL_INTERVAL_S = 5.0
#: Geração de vídeo leva minutos; o teto é generoso mas existe.
_DEFAULT_TIMEOUT_S = 900.0


class VideoTimeoutError(OpenRouterResponseError):
    """O job não atingiu estado terminal dentro do teto de tempo.

    Não é sinônimo de falha do provider — o job pode continuar rodando lá.
    Separado para o chamador poder oferecer "consultar de novo mais tarde".
    """


@dataclass(frozen=True)
class VideoJob:
    id: str
    status: str
    polling_url: str


async def start_video_generation(
    client: OpenRouterClient,
    *,
    model: str,
    prompt: str,
    callback_url: str | None = None,
) -> VideoJob:
    """Dispara a geração e devolve o job — não espera pelo vídeo."""
    payload: dict[str, object] = {"model": model, "prompt": prompt}
    if callback_url:
        payload["callback_url"] = callback_url

    resposta = await client.post_json("/videos", payload)
    job_id = str(resposta.get("id") or "")
    if not job_id:
        # Sem `id` não há como consultar o progresso: o job ficaria rodando e
        # cobrando sem ninguém buscar o resultado.
        msg = "OpenRouter respondeu /videos sem `id` — job impossível de acompanhar"
        raise OpenRouterResponseError(msg)

    return VideoJob(
        id=job_id,
        status=str(resposta.get("status") or "pending"),
        polling_url=str(resposta.get("polling_url") or f"/videos/{job_id}"),
    )


async def generate_video(
    client: OpenRouterClient,
    *,
    model: str,
    prompt: str,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict:
    """Dispara e acompanha até um estado terminal, respeitando o teto."""
    job = await start_video_generation(client, model=model, prompt=prompt)
    limite = time.monotonic() + timeout_s

    while True:
        estado = await client.get_json(job.polling_url)
        status = str(estado.get("status") or "")

        if status == "completed":
            if not estado.get("output"):
                # Concluir sem entregar o vídeo é falha, não sucesso.
                msg = "OpenRouter marcou o vídeo como completed mas sem `output`"
                raise OpenRouterResponseError(msg)
            return estado

        if status in _TERMINAIS_DE_FALHA:
            detalhe = estado.get("error") or "sem detalhe do provider"
            msg = f"geração de vídeo terminou em `{status}`: {detalhe}"
            raise OpenRouterResponseError(msg)

        # Status fora do ciclo documentado (API evoluiu) é tratado como "ainda
        # rodando" até o teto — melhor que estourar KeyError num estado novo.
        if status not in ("pending", "in_progress"):
            logger.warning(
                "openrouter: status de vídeo desconhecido, seguindo o polling",
                extra={"status": status, "job_id": job.id},
            )

        if time.monotonic() >= limite:
            msg = (
                f"geração de vídeo não concluiu em {timeout_s:.0f}s "
                f"(último status: {status or 'desconhecido'}, job {job.id}) — "
                "o job pode seguir rodando no provider"
            )
            raise VideoTimeoutError(msg)

        if poll_interval_s:
            await asyncio.sleep(poll_interval_s)
