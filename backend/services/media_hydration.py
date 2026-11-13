"""Reidrata mídia referenciada no histórico antes do envio ao provider.

O histórico persistido guarda só a referência opaca do asset (``asset_id``);
o ``data:`` URL é removido em ``_message_to_row`` para não inflar o banco.
Como o loop do agente reconstrói cada turno a partir do histórico
persistido, a imagem precisa ser recarregada do ``AssetStore`` na cópia que
vai ao provider. A cópia persistida nunca recebe os bytes.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace

from backend.vtypes.message import ContentBlock, VMessage

logger = logging.getLogger(__name__)

AssetReader = Callable[..., tuple[object, bytes] | None]


def _unavailable_text(block: ContentBlock) -> str:
    name = block.attachment_name or "imagem"
    return f"[Imagem anexada indisponível: {name}]"


def _default_reader() -> AssetReader:
    from backend.services.assets import asset_store

    return asset_store.read


def hydrate_media_blocks(
    messages: Sequence[VMessage],
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    reader: AssetReader | None = None,
) -> list[VMessage]:
    """Devolve cópias das mensagens com imagens de asset reidratadas.

    - Bloco ``image_url`` com ``image_url`` preenchido segue como está.
    - Bloco ``image_url`` sem URL e com ``asset_id`` recebe o ``data:`` URL
      lido do ``AssetStore`` (que valida dono, workspace, thread, tamanho e
      assinatura).
    - Asset ausente, inválido ou de outro dono vira bloco de texto que
      avisa a indisponibilidade, em vez de sumir em silêncio.
    - Bloco ``media`` (anexo que não é imagem) vira texto com a forma
      textual do anexo.
    """
    read = reader or _default_reader()
    hydrated: list[VMessage] = []
    for message in messages:
        if not any(
            (block.kind == "image_url" and not block.image_url) or block.kind == "media"
            for block in message.content
        ):
            hydrated.append(message)
            continue
        blocks: list[ContentBlock] = []
        for block in message.content:
            if block.kind == "media":
                # Anexo não-imagem chega ao provider pela forma textual
                # (conteúdo, texto extraído, transcrição ou referência).
                blocks.append(ContentBlock(kind="text", text=block.text or ""))
                continue
            if block.kind != "image_url" or block.image_url:
                blocks.append(block)
                continue
            loaded = None
            if block.asset_id:
                try:
                    loaded = read(
                        block.asset_id,
                        owner_id=user_id,
                        workspace_id=workspace_id,
                        thread_id=thread_id,
                    )
                except Exception:
                    logger.exception(
                        "media_hydration: falha ao ler asset %s", block.asset_id
                    )
                    loaded = None
            if loaded is None:
                blocks.append(ContentBlock(kind="text", text=_unavailable_text(block)))
                continue
            asset, payload = loaded
            mime_type = str(
                getattr(asset, "mime_type", "") or block.attachment_mime_type or ""
            )
            encoded = base64.b64encode(payload).decode("ascii")
            blocks.append(
                replace(block, image_url=f"data:{mime_type};base64,{encoded}")
            )
        hydrated.append(replace(message, content=blocks))
    return hydrated
