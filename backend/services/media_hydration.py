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

from backend.vtypes.message import ContentBlock, MessageRole, VMessage

logger = logging.getLogger(__name__)

AssetReader = Callable[..., tuple[object, bytes] | None]


def _unavailable_text(block: ContentBlock) -> str:
    name = block.attachment_name or "imagem"
    return f"[Imagem anexada indisponível: {name}]"


def _default_reader() -> AssetReader:
    from backend.services.assets import asset_store

    return asset_store.read


def _latest_user_index(messages: Sequence[VMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == MessageRole.USER:
            return index
    return -1


def hydrate_media_blocks(
    messages: Sequence[VMessage],
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    reader: AssetReader | None = None,
) -> list[VMessage]:
    """Devolve cópias das mensagens com a mídia de asset carregada.

    - Bloco ``image_url`` com ``image_url`` preenchido segue como está.
    - Bloco ``image_url`` sem URL e com ``asset_id`` recebe o ``data:`` URL
      lido do ``AssetStore`` (que valida dono, workspace, thread, tamanho,
      assinatura e digest).
    - Asset de imagem ausente, inválido ou de outro dono vira bloco de texto
      que avisa a indisponibilidade, em vez de sumir em silêncio.
    - Bloco ``media`` da mensagem mais recente do usuário recebe
      ``media_data_url`` quando algum provider aceita o tipo nativamente; o
      cliente do provider decide entre o arquivo e a forma textual
      (``backend/llm/media_parts.py``). Anexos de mensagens anteriores seguem
      só pela forma textual, sem reenviar o arquivo a cada turno.
    """
    from backend.llm.media_parts import wants_bytes

    read = reader or _default_reader()
    latest_user = _latest_user_index(messages)

    def _load(block: ContentBlock) -> tuple[object, bytes] | None:
        if not block.asset_id:
            return None
        try:
            return read(
                block.asset_id,
                owner_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
            )
        except Exception:
            logger.exception("media_hydration: falha ao ler asset %s", block.asset_id)
            return None

    hydrated: list[VMessage] = []
    for index, message in enumerate(messages):
        load_media = index == latest_user
        if not any(
            (block.kind == "image_url" and not block.image_url)
            or (
                load_media
                and block.kind == "media"
                and block.asset_id
                and wants_bytes(
                    block.attachment_mime_type or "", block.attachment_size_bytes
                )
            )
            for block in message.content
        ):
            hydrated.append(message)
            continue
        blocks: list[ContentBlock] = []
        for block in message.content:
            if block.kind == "media":
                if (
                    load_media
                    and block.asset_id
                    and wants_bytes(
                        block.attachment_mime_type or "", block.attachment_size_bytes
                    )
                ):
                    loaded = _load(block)
                    if loaded is not None:
                        _asset, payload = loaded
                        encoded = base64.b64encode(payload).decode("ascii")
                        mime = block.attachment_mime_type or ""
                        blocks.append(
                            replace(
                                block,
                                media_data_url=f"data:{mime};base64,{encoded}",
                            )
                        )
                        continue
                blocks.append(block)
                continue
            if block.kind != "image_url" or block.image_url:
                blocks.append(block)
                continue
            loaded = _load(block)
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
