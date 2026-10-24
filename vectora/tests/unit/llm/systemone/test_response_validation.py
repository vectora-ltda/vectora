"""Request-bound validation and safe error regression tests."""

from __future__ import annotations

import httpx
import pytest

from backend.llm.systemone.api_adapters import ClefApiAdapter, JevApiAdapter
from backend.llm.systemone.clef_local import ClefLocalAdapter
from backend.llm.systemone.errors import SystemOneValidationError
from backend.llm.systemone.policy import DecisionPolicyEngine
from backend.llm.systemone.settings import SystemOneSettings
from backend.llm.systemone.types import (
    ChoiceQuestion,
    ScoreQuestion,
    SystemOneRequest,
    parse_systemone_response,
)


@pytest.mark.parametrize(
    "adapter",
    [
        ClefLocalAdapter(),
        ClefApiAdapter(api_key="test", account_id="test-account"),
        JevApiAdapter(api_key="test"),
    ],
)
@pytest.mark.parametrize(
    "answers",
    [
        None,
        [],
        {},
        {"q": {"type": "score", "score": 12}},
        {"q": {"type": "noul", "probability": 0.9}},
        {"q": {"type": "score", "score": "secret"}},
        {"q": {"type": "score", "score": 2}, "extra": {"type": "score", "score": 2}},
    ],
)
async def test_adapters_reject_invalid_responses(
    adapter: ClefLocalAdapter | ClefApiAdapter | JevApiAdapter,
    answers: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All adapters reject malformed, extra, mismatched and out-of-range answers."""

    async def post(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json={"answers": answers})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    request = SystemOneRequest(
        model="test",
        state="private",
        questions={
            "q": ScoreQuestion(criterion="test", min_score=1, max_score=5),
        },
    )
    with pytest.raises(SystemOneValidationError, match=r"^systemone_invalid_response$"):
        await adapter.decide(request)


@pytest.mark.parametrize(
    "distribution",
    [
        {"a": 0.2, "b": 0.2},
        {"a": 1.0},
        {"a": 0.8, "unexpected": 0.2},
        {"a": 0.1, "b": 0.9},
    ],
)
def test_choice_distribution_matches_request(distribution: dict[str, float]) -> None:
    """Distributions must be complete, normalized and agree with the selection."""
    request = SystemOneRequest(
        model="test",
        state="private",
        questions={
            "q": ChoiceQuestion(options=["a", "b"]),
        },
    )
    with pytest.raises(SystemOneValidationError):
        parse_systemone_response(
            {
                "answers": {
                    "q": {
                        "type": "choice",
                        "selection": "a",
                        "distribution": distribution,
                    }
                }
            },
            request,
            provider="test",
            latency_ms=1,
        )


def test_missing_confidence_requires_review() -> None:
    """A usable probability does not substitute for missing confidence."""
    request = SystemOneRequest(
        model="test",
        state="private",
        questions={
            "q": ScoreQuestion(criterion="test"),
        },
    )
    response = parse_systemone_response(
        {"answers": {"q": {"type": "score", "score": 0.9}}},
        request,
        provider="test",
        latency_ms=1,
    )
    result = DecisionPolicyEngine(SystemOneSettings(enabled=True)).evaluate_response(
        response
    )
    assert result.requires_hitl
    assert response.raw_payload is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "secret",
        {"answers": {"q": {"type": "score", "score": 0.5}}, "usage": []},
    ],
)
def test_invalid_envelopes_are_safe(payload: object) -> None:
    """Invalid envelopes and usage never escape as untyped exceptions."""
    request = SystemOneRequest(
        model="test",
        state="private",
        questions={
            "q": ScoreQuestion(criterion="test"),
        },
    )
    with pytest.raises(SystemOneValidationError, match=r"^systemone_invalid_response$"):
        parse_systemone_response(payload, request, provider="test", latency_ms=1)


@pytest.mark.parametrize(
    "adapter",
    [
        ClefLocalAdapter(),
        ClefApiAdapter(api_key="test", account_id="test-account"),
        JevApiAdapter(api_key="test"),
    ],
)
async def test_http_errors_do_not_expose_response_body(
    adapter: ClefLocalAdapter | ClefApiAdapter | JevApiAdapter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider bodies must not appear in returned policy reasons or logs."""

    async def post(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(400, text="secret-state-and-credential")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    request = SystemOneRequest(
        model="test",
        state="private",
        questions={
            "q": ScoreQuestion(criterion="test"),
        },
    )
    result = await DecisionPolicyEngine(
        SystemOneSettings(enabled=True)
    ).execute_decision(adapter, request)
    assert result.requires_hitl
    assert "secret-state-and-credential" not in result.reason
    assert "secret-state-and-credential" not in caplog.text
