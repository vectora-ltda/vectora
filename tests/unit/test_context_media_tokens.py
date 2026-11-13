"""A estimativa de contexto conta as imagens geradas de PDF e vídeo."""

from __future__ import annotations

from backend.services.context_compaction import (
    _IMAGE_TOKEN_ESTIMATE,
    _message_tokens,
    count_previews_for,
    pending_preview_assets,
)
from backend.services.context_planner import plan_context
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _attachment(asset_id: str, previews: int) -> VMessage:
    return VMessage(
        role=MessageRole.USER,
        content=[
            ContentBlock(kind="text", text="resuma"),
            ContentBlock(
                kind="media",
                media_kind="document",
                text="[r.pdf] texto extraído",
                asset_id=asset_id,
                attachment_name="r.pdf",
                attachment_mime_type="application/pdf",
                media_preview_asset_ids=[f"{asset_id}-p{i}" for i in range(previews)],
            ),
        ],
    )


def _text(role: MessageRole, words: int) -> VMessage:
    return VMessage(
        role=role, content=[ContentBlock(kind="text", text="palavra " * words)]
    )


def test_so_a_mensagem_mais_recente_do_usuario_leva_previas() -> None:
    old = _attachment("antigo", 4)
    latest = _attachment("novo", 6)
    reply = _text(MessageRole.ASSISTANT, 5)

    assert pending_preview_assets([old, reply, latest]) == {"novo"}
    assert pending_preview_assets([old, reply]) == {"antigo"}
    assert pending_preview_assets([reply]) == frozenset()


def test_previas_pesam_como_imagens_so_quando_serao_enviadas() -> None:
    latest = _attachment("novo", 6)
    base = _message_tokens(latest)
    with count_previews_for([latest]):
        counted = _message_tokens(latest)
    assert counted - base == 6 * _IMAGE_TOKEN_ESTIMATE
    # Fora do contexto de planejamento, nada muda.
    assert _message_tokens(latest) == base


def test_anexo_antigo_nao_pesa_como_imagem() -> None:
    old = _attachment("antigo", 6)
    latest = _attachment("novo", 2)
    with count_previews_for([old, latest]):
        assert _message_tokens(old) == _message_tokens(_attachment("antigo", 0))


def test_planejador_compacta_o_historico_para_caberem_as_previas() -> None:
    history = [
        _text(role, 1500)
        for _ in range(3)
        for role in (MessageRole.USER, MessageRole.ASSISTANT)
    ]
    plain = [*history, _attachment("novo", 0)]
    with_previews = [*history, _attachment("novo", 8)]
    budget = 16_000

    kept_plain = plan_context(plain, max_tokens=budget).messages
    kept_previews = plan_context(with_previews, max_tokens=budget).messages

    # Sem prévias tudo cabe; com 8 imagens (~12,8 mil tokens) o histórico
    # antigo precisa ser compactado, e a mensagem do anexo continua inteira.
    assert len(kept_plain) == len(plain)
    assert len(kept_previews) < len(kept_plain)
    assert kept_previews[-1] is not None
    assert kept_previews[-1].content[1].asset_id == "novo"
