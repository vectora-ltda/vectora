"""Imagens produzidas por ferramentas (screenshots) entregues ao modelo.

A ferramenta salva a imagem como asset da sessão e devolve um JSON curto com
``url`` (exibida na interface) e ``model_image`` (referência do asset). O lote
de ferramentas (``backend/engine/tool_batch.py``) transforma essa referência
em bloco ``image_url`` na mensagem da ferramenta; os bytes nunca entram no
texto do resultado nem no histórico persistido.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from backend.services.media_types import MediaInfo, sniff_mime
from backend.vtypes.message import ContentBlock

if TYPE_CHECKING:
    from backend.tools.context import ToolContext

#: Chave do JSON do resultado que pede o envio da imagem ao modelo.
MODEL_IMAGE_KEY = "model_image"


def save_tool_image(
    ctx: ToolContext, payload: bytes, *, name: str
) -> dict[str, Any] | None:
    """Salva a imagem e devolve os campos do resultado, ou ``None`` se falhar."""
    from backend.services.chat_attachments import persist_media

    mime_type = sniff_mime(payload, name)
    if mime_type is None or not mime_type.startswith("image/"):
        return None
    saved = persist_media(
        payload,
        MediaInfo(kind="image", mime_type=mime_type),
        thread_id=ctx.thread_id,
        user_id=ctx.user_id or "local",
        workspace_id=ctx.workspace_id or "",
    )
    if saved is None:
        return None
    path, asset_id, _sha256 = saved
    return {
        "url": f"/threads/{ctx.thread_id}/assets/{asset_id}",
        "path": str(path),
        "bytes": len(payload),
        MODEL_IMAGE_KEY: {
            "asset_id": asset_id,
            "name": name,
            "mime_type": mime_type,
            "size_bytes": len(payload),
        },
    }


#: Chave com várias imagens (ex.: ferramenta MCP que devolve mais de uma).
MODEL_IMAGES_KEY = "model_images"


def _image_block(image: object) -> ContentBlock | None:
    if not isinstance(image, dict) or not isinstance(image.get("asset_id"), str):
        return None
    size = image.get("size_bytes")
    return ContentBlock(
        kind="image_url",
        asset_id=image["asset_id"],
        attachment_name=str(image.get("name") or "imagem.png"),
        attachment_mime_type=str(image.get("mime_type") or "image/png"),
        attachment_size_bytes=size if isinstance(size, int) else None,
    )


def tool_image_blocks(result_text: str) -> list[ContentBlock]:
    """Blocos de imagem pedidos por um resultado de ferramenta."""
    if MODEL_IMAGE_KEY not in result_text:
        return []
    try:
        data = json.loads(result_text)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    images = data.get(MODEL_IMAGES_KEY)
    candidates = images if isinstance(images, list) else [data.get(MODEL_IMAGE_KEY)]
    return [block for image in candidates if (block := _image_block(image))]
