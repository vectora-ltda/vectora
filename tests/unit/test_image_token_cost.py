"""O custo em tokens de uma imagem é aprendido por modelo a partir do uso."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.services import image_token_cost as cost
from backend.services.context_compaction import _message_tokens, count_previews_for
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "image_token_cost.json"
    monkeypatch.setattr(cost, "_store_path", lambda: path)
    cost.reset_for_tests()
    yield path
    cost.reset_for_tests()


def _imagem() -> VMessage:
    return VMessage(
        role=MessageRole.USER,
        content=[
            ContentBlock(kind="text", text="olhe"),
            ContentBlock(kind="image_url", image_url="data:image/png;base64,AAAA"),
        ],
    )


def test_sem_medicao_vale_o_padrao() -> None:
    assert cost.cost_for("modelo-novo") == cost.DEFAULT_IMAGE_TOKENS
    assert cost.cost_for(None) == cost.DEFAULT_IMAGE_TOKENS


def test_primeira_medicao_vira_o_custo_do_modelo() -> None:
    learned = cost.observe("m", input_tokens=2_000, text_tokens=400, image_count=2)
    assert learned == 800
    assert cost.cost_for("m") == 800
    assert cost.cost_for("outro") == cost.DEFAULT_IMAGE_TOKENS


def test_medicoes_seguintes_entram_numa_media_movel() -> None:
    cost.observe("m", input_tokens=1_000, text_tokens=0, image_count=1)
    learned = cost.observe("m", input_tokens=2_000, text_tokens=0, image_count=1)
    assert learned == round(1_000 * 0.7 + 2_000 * 0.3)


@pytest.mark.parametrize(
    ("entrada", "texto", "imagens"),
    [(1_000, 0, 0), (0, 0, 1), (100, 90, 1), (50_000, 0, 1)],
)
def test_medicao_invalida_e_descartada(entrada: int, texto: int, imagens: int) -> None:
    assert (
        cost.observe("m", input_tokens=entrada, text_tokens=texto, image_count=imagens)
        is None
    )
    assert cost.cost_for("m") == cost.DEFAULT_IMAGE_TOKENS


def test_custo_aprendido_sobrevive_a_reinicio(store: Path) -> None:
    cost.observe("m", input_tokens=1_500, text_tokens=0, image_count=1)
    assert store.exists()
    cost.reset_for_tests()
    assert cost.cost_for("m") == 1_500


def test_arquivo_corrompido_cai_no_padrao(store: Path) -> None:
    store.write_text("{não é json", encoding="utf-8")
    assert cost.cost_for("m") == cost.DEFAULT_IMAGE_TOKENS


def test_conta_imagens_de_anexo_e_previas() -> None:
    com_previas = VMessage(
        role=MessageRole.USER,
        content=[
            ContentBlock(kind="media", text="[r.pdf]", media_preview_urls=["a", "b"]),
        ],
    )
    assert cost.count_images([_imagem(), com_previas]) == 3
    assert cost.count_images([]) == 0


def test_observe_turn_desconta_o_texto_e_aprende() -> None:
    mensagens = [_imagem()]
    learned = cost.observe_turn("m", mensagens, None, {"input_tokens": 1_700})
    assert learned is not None
    assert 1_600 <= learned <= 1_700
    assert cost.cost_for("m") == learned


def test_observe_turn_sem_imagem_ou_sem_uso_nao_aprende() -> None:
    texto = VMessage(
        role=MessageRole.USER, content=[ContentBlock(kind="text", text="oi")]
    )
    assert cost.observe_turn("m", [texto], None, {"input_tokens": 100}) is None
    assert cost.observe_turn("m", [_imagem()], None, None) is None


def test_planejador_usa_o_custo_aprendido_do_modelo() -> None:
    mensagem = _imagem()
    cost.observe("m", input_tokens=900, text_tokens=0, image_count=1)
    with count_previews_for([mensagem], "m"):
        aprendido = _message_tokens(mensagem)
    with count_previews_for([mensagem], "desconhecido"):
        padrao = _message_tokens(mensagem)
    assert padrao - aprendido == cost.DEFAULT_IMAGE_TOKENS - 900
