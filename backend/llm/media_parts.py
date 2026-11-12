"""Decisão de modalidade dos anexos ``media`` por provider.

Um bloco ``ContentBlock(kind="media")`` chega aos clientes com duas formas:
o arquivo (``media_data_url``, preenchido só na cópia do provider) e a forma
textual (``text``). Cada cliente chama :func:`prepare_content` com o seu
provider; o bloco segue nativo quando o provider aceita aquele tipo e o
arquivo cabe no limite dele, e vira texto em qualquer outro caso.

A decisão é a mesma para todos os clientes e fica registrada no run
(:func:`media_routing_evidence`), então o usuário vê o que foi enviado como
arquivo e o que foi enviado como texto.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Literal

from backend.vtypes.message import ContentBlock, MessageRole, VMessage

MB = 1024 * 1024

MediaMode = Literal["native", "text"]

#: Tipos aceitos nativamente e teto do arquivo inline, por MIME ou família.
AcceptedMedia = Mapping[str, int]

#: Providers com API própria: o tipo aceito não depende do modelo.
NATIVE_MEDIA: dict[str, dict[str, int]] = {
    "anthropic": {"application/pdf": 32 * MB},
    "google_genai": {
        "application/pdf": 20 * MB,
        "audio/*": 20 * MB,
        "video/*": 20 * MB,
    },
    "openai": {"application/pdf": 32 * MB},
}

#: Gateways (OpenRouter, 9router): o catálogo informa, por modelo, as
#: modalidades de entrada. Cada modalidade libera estes tipos.
GATEWAY_MEDIA_BY_MODALITY: dict[str, dict[str, int]] = {
    "file": {"application/pdf": 20 * MB},
    "audio": {"audio/mpeg": 20 * MB, "audio/wav": 20 * MB},
    "video": {"video/*": 20 * MB},
}

#: Gateways que consultam o catálogo por modelo.
CATALOG_GATEWAYS = frozenset({"openrouter", "nine_router"})

MediaCapability = str | AcceptedMedia


def normalize_provider(provider: str) -> str:
    return provider.replace("-", "_")


def _accepted(capability: MediaCapability) -> AcceptedMedia:
    if isinstance(capability, str):
        return NATIVE_MEDIA.get(normalize_provider(capability), {})
    return capability


def native_limit(capability: MediaCapability, mime_type: str) -> int | None:
    """Teto em bytes para envio nativo, ou ``None`` se não é aceito.

    ``capability`` é o nome do provider (tabela fixa) ou o mapa já resolvido
    para o modelo (:func:`resolve_native_media`).
    """
    accepted = _accepted(capability)
    if mime_type in accepted:
        return accepted[mime_type]
    family = mime_type.split("/", 1)[0]
    return accepted.get(f"{family}/*")


def accepts_native(
    capability: MediaCapability, mime_type: str, size: int | None
) -> bool:
    limit = native_limit(capability, mime_type)
    return limit is not None and (size or 0) <= limit


def wants_bytes(mime_type: str, size: int | None) -> bool:
    """Algum provider ou gateway conhecido aceitaria este arquivo?"""
    return any(
        accepts_native(accepted, mime_type, size)
        for accepted in (*NATIVE_MEDIA.values(), *GATEWAY_MEDIA_BY_MODALITY.values())
    )


def media_for_modalities(modalities: Iterable[str]) -> dict[str, int]:
    """Tipos aceitos a partir das modalidades de entrada de um catálogo."""
    accepted: dict[str, int] = {}
    for modality in modalities:
        accepted.update(GATEWAY_MEDIA_BY_MODALITY.get(modality, {}))
    return accepted


async def resolve_native_media(model_id: str) -> AcceptedMedia:
    """Tipos aceitos nativamente pelo modelo ``provider:modelo``.

    Providers com API própria usam a tabela fixa. OpenRouter e 9router
    consultam o catálogo do gateway; modelo ausente ou catálogo indisponível
    resultam em nenhum tipo nativo (os anexos seguem como texto).
    """
    provider, _sep, model = model_id.partition(":")
    provider = normalize_provider(provider)
    if provider in NATIVE_MEDIA:
        return NATIVE_MEDIA[provider]
    if provider not in CATALOG_GATEWAYS or not model:
        return {}
    from backend.api.handlers import provider_routing

    lookup = (
        provider_routing.openrouter_model_modalities
        if provider == "openrouter"
        else provider_routing.nine_router_model_modalities
    )
    try:
        modalities = await lookup(model)
    except Exception:
        return {}
    return media_for_modalities(modalities or ())


def route_block(block: ContentBlock, capability: MediaCapability) -> MediaMode:
    """Modo do bloco ``media`` para o provider ou modelo."""
    if (
        block.kind == "media"
        and block.media_data_url
        and accepts_native(
            capability, block.attachment_mime_type or "", block.attachment_size_bytes
        )
    ):
        return "native"
    return "text"


def prepare_content(
    content: list[ContentBlock], capability: MediaCapability
) -> list[ContentBlock]:
    """Mantém nativos os anexos aceitos e converte os demais em texto."""
    if not any(block.kind == "media" for block in content):
        return content
    prepared: list[ContentBlock] = []
    for block in content:
        if block.kind != "media" or route_block(block, capability) == "native":
            prepared.append(block)
        else:
            prepared.append(ContentBlock(kind="text", text=block.text or ""))
    return prepared


def _is_tool_image(message: VMessage, block: ContentBlock) -> bool:
    return (
        message.role == MessageRole.TOOL
        and block.kind == "image_url"
        and bool(block.image_url)
    )


def has_native_media(messages: Iterable[VMessage]) -> bool:
    """Há anexo enviado como arquivo ou imagem de ferramenta no payload?"""
    return any(
        (block.kind == "media" and block.media_data_url)
        or _is_tool_image(message, block)
        for message in messages
        for block in message.content
    )


def downgrade_to_text(messages: list[VMessage]) -> list[VMessage]:
    """Remove arquivos inline e imagens de ferramentas.

    Os anexos seguem pela forma textual; imagens de ferramentas viram uma
    nota com o nome do arquivo salvo.
    """
    downgraded: list[VMessage] = []
    for message in messages:
        if not any(
            (block.kind == "media" and block.media_data_url)
            or _is_tool_image(message, block)
            for block in message.content
        ):
            downgraded.append(message)
            continue
        content: list[ContentBlock] = []
        for block in message.content:
            if block.kind == "media":
                content.append(replace(block, media_data_url=None))
            elif _is_tool_image(message, block):
                content.append(
                    ContentBlock(
                        kind="text",
                        text=(
                            "\n[Imagem da ferramenta não enviada ao modelo: "
                            f"{block.attachment_name or 'imagem'}]"
                        ),
                    )
                )
            else:
                content.append(block)
        downgraded.append(replace(message, content=content))
    return downgraded


def split_data_url(data_url: str) -> tuple[str, str]:
    """``data:<mime>;base64,<dados>`` → ``(mime, dados)``."""
    header, _, data = data_url.partition(";base64,")
    return header.removeprefix("data:"), data


def media_routing_evidence(
    messages: Iterable[VMessage], capability: MediaCapability, model_id: str
) -> list[dict[str, object]]:
    """Registro auditável de como cada anexo foi enviado ao provider."""
    entries: list[dict[str, object]] = []
    for message in messages:
        for block in message.content:
            if block.kind != "media":
                continue
            mode = route_block(block, capability)
            reason = (
                "provider aceita o tipo"
                if mode == "native"
                else (
                    "arquivo não carregado para este turno"
                    if not block.media_data_url
                    else "provider não aceita o tipo ou o tamanho"
                )
            )
            entries.append(
                {
                    "name": block.attachment_name or "",
                    "media_kind": block.media_kind or "file",
                    "mime_type": block.attachment_mime_type or "",
                    "size_bytes": block.attachment_size_bytes or 0,
                    "sha256": block.media_sha256 or "",
                    "mode": mode,
                    "reason": reason,
                    "model": model_id,
                }
            )
    return entries
