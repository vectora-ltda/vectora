"""Custo em tokens de uma imagem, aprendido por modelo a partir do uso.

O planejador de contexto precisa saber quanto cada imagem pesa antes de
chamar o modelo. Sem dado, usa uma estimativa fixa (``DEFAULT_IMAGE_TOKENS``).
Depois de cada chamada com imagens, o provider informa os tokens de entrada
reais; a parte que o texto não explica, dividida pelas imagens enviadas, é o
custo observado por imagem. Uma média móvel por modelo guarda esse valor e o
planejador passa a usá-lo nas chamadas seguintes.

Os valores ficam num arquivo JSON no diretório de dados do app. Qualquer falha
de leitura ou escrita cai na estimativa fixa, sem afetar a conversa.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.vtypes.message import VMessage

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_TOKENS = 1_600
#: Faixa aceita para um custo por imagem; fora dela a medição é descartada
#: (o texto estimado a mais ou a menos distorce chamadas com poucas imagens).
MIN_IMAGE_TOKENS = 64
MAX_IMAGE_TOKENS = 8_000
#: Peso de cada nova medição na média móvel.
_SMOOTHING = 0.3

_lock = threading.Lock()
_costs: dict[str, float] | None = None


def _store_path() -> Path:
    try:
        from backend.settings import settings

        data_dir = getattr(settings, "data_dir", "") or ""
        if data_dir:
            return Path(str(data_dir)) / "image_token_cost.json"
    except Exception:
        logger.debug("image_token_cost: settings indisponível", exc_info=True)
    home = os.environ.get("VECTORA_HOME")
    base = Path(home) if home else Path.home() / ".vectora"
    return base / "data" / "image_token_cost.json"


def _load() -> dict[str, float]:
    global _costs
    if _costs is None:
        loaded: dict[str, float] = {}
        try:
            raw = json.loads(_store_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                loaded = {
                    str(model): float(value)
                    for model, value in raw.items()
                    if isinstance(value, int | float)
                    and MIN_IMAGE_TOKENS <= value <= MAX_IMAGE_TOKENS
                }
        except (OSError, ValueError):
            loaded = {}
        _costs = loaded
    return _costs


def _save(costs: dict[str, float]) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(costs, sort_keys=True), encoding="utf-8")
        temp.replace(path)
    except OSError:
        logger.debug("image_token_cost: não foi possível gravar", exc_info=True)


def cost_for(model: str | None) -> int:
    """Tokens por imagem para o modelo; sem medição, o padrão."""
    if not model:
        return DEFAULT_IMAGE_TOKENS
    with _lock:
        learned = _load().get(model)
    return round(learned) if learned is not None else DEFAULT_IMAGE_TOKENS


def observe(
    model: str | None,
    *,
    input_tokens: int,
    text_tokens: int,
    image_count: int,
) -> int | None:
    """Registra uma medição e devolve o custo por imagem aprendido.

    ``input_tokens`` vem do provider; ``text_tokens`` é a estimativa do que não
    é imagem (mensagens e definição das ferramentas). Sem imagens, sem tokens
    informados ou com resultado fora da faixa, nada é gravado.
    """
    if not model or image_count <= 0 or input_tokens <= 0:
        return None
    per_image = (input_tokens - text_tokens) / image_count
    if not MIN_IMAGE_TOKENS <= per_image <= MAX_IMAGE_TOKENS:
        return None
    with _lock:
        costs = _load()
        previous = costs.get(model)
        updated = (
            per_image
            if previous is None
            else previous * (1 - _SMOOTHING) + per_image * _SMOOTHING
        )
        costs[model] = updated
        _save(costs)
    return round(updated)


def count_images(messages: Sequence[VMessage]) -> int:
    """Imagens que seguem ao provider nestas mensagens (anexos, ferramentas e
    prévias de PDF e vídeo)."""
    total = 0
    for message in messages:
        for block in message.content:
            if block.kind == "image_url" and block.image_url:
                total += 1
            total += len(block.media_preview_urls or ())
    return total


def observe_turn(
    model: str | None,
    messages: Sequence[VMessage],
    tools: object,
    usage: Mapping[str, int] | None,
) -> int | None:
    """Aprende o custo por imagem de uma chamada ao modelo.

    O texto das mensagens e a definição das ferramentas são estimados e
    descontados dos tokens de entrada informados pelo provider.
    """
    images = count_images(messages)
    if images == 0 or not usage:
        return None
    from backend.services.text import text_service

    text_tokens = len(messages) * 4 + text_service.count_tokens(
        "\n".join(
            message.model_text()
            + "".join(str(call.args) for call in message.tool_calls or ())
            for message in messages
        )
    )
    if tools:
        text_tokens += text_service.count_tokens(str(tools))
    return observe(
        model,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        text_tokens=text_tokens,
        image_count=images,
    )


def reset_for_tests() -> None:
    """Descarta o cache em memória (o arquivo não é apagado)."""
    global _costs
    with _lock:
        _costs = None
