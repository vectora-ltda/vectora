from __future__ import annotations

import httpx
import pytest

from backend.llm.systemone.errors import SystemOneCapabilityError
from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.settings import SystemOneSettings, get_systemone_provider
from backend.llm.systemone.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneRequest,
)


def _request() -> SystemOneRequest:
    return SystemOneRequest(
        model="convaiinnovations/laya",
        state="A produção está indisponível e o cliente ameaça cancelar.",
        questions={
            "department": ChoiceQuestion(
                instructions="Escolha a fila.",
                options=["infra", "billing"],
                criteria={
                    "infra": "incidentes e indisponibilidade",
                    "billing": "cobrança",
                },
            ),
            "urgency": ScoreQuestion(
                criterion="Urgência do incidente",
                min_score=0,
                max_score=2,
                levels=["baixa", "média", "crítica"],
            ),
            "churn": NoulQuestion(criterion="O cliente ameaça cancelar?"),
        },
    )


@pytest.mark.asyncio
async def test_laya_serializes_typed_questions_and_normalizes_response() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "department": {
                        "choice": "infra",
                        "probabilities": {"infra": 0.9, "billing": 0.1},
                        "answer_confidence": 0.9,
                    },
                    "urgency": {"score": 2, "probabilities": [0.01, 0.04, 0.95]},
                    "churn": {"noul": 0.8, "answer_confidence": 0.8},
                },
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await LayaLocalAdapter(client=client).decide(_request())

    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["questions"]["department"]["criteria"]["infra"] == (
        "incidentes e indisponibilidade"
    )
    assert payload["questions"]["urgency"]["criteria"] == ["baixa", "média", "crítica"]
    assert isinstance(response.answers["department"], ChoiceAnswer)
    assert response.answers["department"].selection == "infra"
    assert isinstance(response.answers["urgency"], ScoreAnswer)
    assert response.answers["urgency"].score == 2
    assert isinstance(response.answers["churn"], NoulAnswer)
    assert response.answers["churn"].probability == 0.8
    assert response.metadata.usage.prompt_tokens == 12
    assert response.metadata.usage.completion_tokens == 3


@pytest.mark.asyncio
async def test_laya_rejects_media() -> None:
    request = _request()
    request.media_urls = ["file:///tmp/image.png"]
    with pytest.raises(SystemOneCapabilityError, match=r"não.*imagens|não aceita"):
        await LayaLocalAdapter().decide(request)


def test_laya_provider_factory() -> None:
    provider = get_systemone_provider(
        SystemOneSettings(provider="laya-local", laya_base_url="http://laya:8000")
    )
    assert isinstance(provider, LayaLocalAdapter)
    assert provider.provider_id == "laya-local"
