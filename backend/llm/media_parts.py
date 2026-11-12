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

from collections.abc import Iterable
from dataclasses import replace
from typing import Literal

from backend.vtypes.message import ContentBlock, VMessage

MB = 1024 * 1024

MediaMode = Literal["native", "text"]

#: Tipos aceitos nativamente por provider e teto do arquivo inline.
#: Gateways (OpenRouter, 9router, Ollama, llama.cpp) ficam em texto até
#: haver metadado de modalidade por modelo.
NATIVE_MEDIA: dict[str, dict[str, int]] = {
    "anthropic": {"application/pdf": 32 * MB},
    "google_genai": {
        "application/pdf": 20 * MB,
        "audio/*": 20 * MB,
        "video/*": 20 * MB,
    },
    "openai": {"application/pdf": 32 * MB},
}


def normalize_provider(provider: str) -> str:
    return provider.replace("-", "_")


def native_limit(provider: str, mime_type: str) -> int | None:
    """Teto em bytes para envio nativo, ou ``None`` se não é aceito."""
    accepted = NATIVE_MEDIA.get(normalize_provider(provider), {})
    if mime_type in accepted:
        return accepted[mime_type]
    family = mime_type.split("/", 1)[0]
    return accepted.get(f"{family}/*")


def accepts_native(provider: str, mime_type: str, size: int | None) -> bool:
    limit = native_limit(provider, mime_type)
    return limit is not None and (size or 0) <= limit


def wants_bytes(mime_type: str, size: int | None) -> bool:
    """Algum provider conhecido aceitaria este arquivo nativamente?"""
    return any(accepts_native(provider, mime_type, size) for provider in NATIVE_MEDIA)


def route_block(block: ContentBlock, provider: str) -> MediaMode:
    """Modo do bloco ``media`` para o provider."""
    if (
        block.kind == "media"
        and block.media_data_url
        and accepts_native(
            provider, block.attachment_mime_type or "", block.attachment_size_bytes
        )
    ):
        return "native"
    return "text"


def prepare_content(content: list[ContentBlock], provider: str) -> list[ContentBlock]:
    """Mantém nativos os anexos aceitos e converte os demais em texto."""
    if not any(block.kind == "media" for block in content):
        return content
    prepared: list[ContentBlock] = []
    for block in content:
        if block.kind != "media" or route_block(block, provider) == "native":
            prepared.append(block)
        else:
            prepared.append(ContentBlock(kind="text", text=block.text or ""))
    return prepared


def has_native_media(messages: Iterable[VMessage]) -> bool:
    return any(
        block.kind == "media" and block.media_data_url
        for message in messages
        for block in message.content
    )


def downgrade_to_text(messages: list[VMessage]) -> list[VMessage]:
    """Remove os arquivos inline; os anexos seguem só pela forma textual."""
    downgraded: list[VMessage] = []
    for message in messages:
        if not any(
            block.kind == "media" and block.media_data_url for block in message.content
        ):
            downgraded.append(message)
            continue
        downgraded.append(
            replace(
                message,
                content=[
                    replace(block, media_data_url=None)
                    if block.kind == "media"
                    else block
                    for block in message.content
                ],
            )
        )
    return downgraded


def split_data_url(data_url: str) -> tuple[str, str]:
    """``data:<mime>;base64,<dados>`` → ``(mime, dados)``."""
    header, _, data = data_url.partition(";base64,")
    return header.removeprefix("data:"), data


def media_routing_evidence(
    messages: Iterable[VMessage], provider: str, model_id: str
) -> list[dict[str, object]]:
    """Registro auditável de como cada anexo foi enviado ao provider."""
    entries: list[dict[str, object]] = []
    for message in messages:
        for block in message.content:
            if block.kind != "media":
                continue
            mode = route_block(block, provider)
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
