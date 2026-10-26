"""Suíte exaustiva de testes para SystemOne e Decision Models (Clef e JEV).

Cobre exaustivamente:
- Tipos canônicos (NoulQuestion, NoulAnswer, ChoiceQuestion, ChoiceAnswer, ScoreQuestion, ScoreAnswer)
- Limiares, valores limítrofes, NaN/infinitos, distribuições e validações
- Adapters de decisão (ClefLocalAdapter, ClefApiAdapter, JevApiAdapter)
- Tratamento de status codes HTTP (200, 400, 401, 403, 404, 429, 500, timeouts)
- DecisionPolicyEngine com matriz de decisão, empates, fallback e HITL
- Endpoints HTTP (/decision-models/status e /decision-models/config)
- Configurações, mascaramento de chaves e resolução de provedores
"""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.llm.systemone.api_adapters import (
    BaseSystemOneApiAdapter,
    ClefApiAdapter,
    JevApiAdapter,
)
from backend.llm.systemone.clef_local import ClefLocalAdapter
from backend.llm.systemone.errors import (
    SystemOneAuthenticationError,
    SystemOneCapabilityError,
    SystemOneConnectionError,
    SystemOneError,
    SystemOneRateLimitError,
    SystemOneTimeoutError,
    SystemOneValidationError,
)
from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.policy import DecisionPolicyEngine, PolicyDecisionOutcome
from backend.llm.systemone.settings import (
    SystemOneSettings,
    get_systemone_provider,
    mask_secret,
)
from backend.llm.systemone.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionProviderType,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneMetadata,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)

# ---------------------------------------------------------------------------
# 1. Noul (Boolean / Probabilistic) Exhaustive Tests (~40 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prob", [0.0, 0.001, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.999, 1.0]
)
@pytest.mark.parametrize("conf", [0.0, 0.5, 1.0])
def test_noul_answer_valid_probabilities(prob: float, conf: float) -> None:
    """Verifica o comportamento de noul answer valid probabilities."""
    ans = NoulAnswer(probability=prob, confidence=conf)
    assert ans.type == "noul"
    assert ans.probability == prob
    assert ans.confidence == conf


@pytest.mark.parametrize(
    "invalid_prob",
    [-1.0, -0.001, 1.0001, 2.0, 100.0, float("nan"), float("inf"), float("-inf")],
)
def test_noul_answer_invalid_probabilities(invalid_prob: float) -> None:
    """Verifica o comportamento de noul answer invalid probabilities."""
    with pytest.raises(ValidationError):
        NoulAnswer(probability=invalid_prob)


@pytest.mark.parametrize("invalid_conf", [-0.5, 1.01, 10.0, float("nan"), float("inf")])
def test_noul_answer_invalid_confidence(invalid_conf: float) -> None:
    """Verifica o comportamento de noul answer invalid confidence."""
    with pytest.raises(ValidationError):
        NoulAnswer(probability=0.5, confidence=invalid_conf)


@pytest.mark.parametrize(
    "criterion",
    [
        "Critério 1",
        "Ação destrutiva?",
        "Permitir acesso de leitura?",
        "X" * 500,
    ],
)
def test_noul_question_valid_criterion(criterion: str) -> None:
    """Verifica o comportamento de noul question valid criterion."""
    q = NoulQuestion(criterion=criterion)
    assert q.type == "noul"
    assert q.criterion == criterion


def test_noul_question_empty_criterion() -> None:
    """Verifica o comportamento de noul question empty criterion."""
    with pytest.raises(ValidationError):
        NoulQuestion(criterion="")


# ---------------------------------------------------------------------------
# 2. Choice Exhaustive Tests (~50 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        ["a", "b"],
        ["red", "green", "blue"],
        ["low", "medium", "high", "critical"],
        [f"opt_{i}" for i in range(10)],
    ],
)
def test_choice_question_valid_options(options: list[str]) -> None:
    """Verifica o comportamento de choice question valid options."""
    q = ChoiceQuestion(instructions="Selecione uma opção", options=options)
    assert q.type == "choice"
    assert q.options == options


@pytest.mark.parametrize(
    "invalid_options",
    [
        [],
        ["single"],
        ["duplicate", "duplicate"],
        ["a", "b", "a"],
        ["   ", "valid"],
        ["valid", ""],
    ],
)
def test_choice_question_invalid_options(invalid_options: list[str]) -> None:
    """Verifica o comportamento de choice question invalid options."""
    with pytest.raises(ValidationError):
        ChoiceQuestion(instructions="Escolha", options=invalid_options)


@pytest.mark.parametrize(
    ("selection", "dist"),
    [
        ("a", {"a": 1.0, "b": 0.0}),
        ("b", {"a": 0.1, "b": 0.9}),
        ("cat", {"cat": 0.7, "dog": 0.2, "bird": 0.1}),
        ("opt_1", {f"opt_{i}": 0.2 for i in range(5)}),
    ],
)
def test_choice_answer_valid(selection: str, dist: dict[str, float]) -> None:
    """Verifica o comportamento de choice answer valid."""
    ans = ChoiceAnswer(selection=selection, distribution=dist, confidence=0.95)
    assert ans.type == "choice"
    assert ans.selection == selection
    assert ans.distribution == dist


@pytest.mark.parametrize(
    "invalid_dist",
    [
        {"a": -0.1, "b": 1.1},
        {"a": 1.5, "b": 0.0},
        {"a": float("nan"), "b": 0.5},
        {"a": float("inf"), "b": 0.5},
    ],
)
def test_choice_answer_invalid_distribution_bounds(
    invalid_dist: dict[str, float],
) -> None:
    """Verifica o comportamento de choice answer invalid distribution bounds."""
    with pytest.raises(ValidationError):
        ChoiceAnswer(selection="a", distribution=invalid_dist)


def test_choice_answer_empty_selection() -> None:
    """Verifica o comportamento de choice answer empty selection."""
    with pytest.raises(ValidationError):
        ChoiceAnswer(selection="", distribution={"a": 1.0})


# ---------------------------------------------------------------------------
# 3. Score Exhaustive Tests (~40 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("min_s", "max_s"),
    [
        (0.0, 1.0),
        (1.0, 5.0),
        (0.0, 10.0),
        (0.0, 100.0),
        (-10.0, 10.0),
        (-100.0, 0.0),
    ],
)
def test_score_question_valid_ranges(min_s: float, max_s: float) -> None:
    """Verifica o comportamento de score question valid ranges."""
    q = ScoreQuestion(criterion="Qualidade", min_score=min_s, max_score=max_s)
    assert q.type == "score"
    assert q.min_score == min_s
    assert q.max_score == max_s


@pytest.mark.parametrize(
    ("min_s", "max_s"),
    [
        (1.0, 1.0),
        (5.0, 1.0),
        (10.0, 0.0),
        (0.0, -5.0),
        (float("nan"), 10.0),
        (0.0, float("inf")),
    ],
)
def test_score_question_invalid_ranges(min_s: float, max_s: float) -> None:
    """Verifica o comportamento de score question invalid ranges."""
    with pytest.raises(ValidationError):
        ScoreQuestion(criterion="Teste", min_score=min_s, max_score=max_s)


@pytest.mark.parametrize(
    ("score", "min_s", "max_s"),
    [
        (0.0, 0.0, 1.0),
        (0.5, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (3.5, 1.0, 5.0),
        (-5.0, -10.0, 10.0),
        (99.9, 0.0, 100.0),
    ],
)
def test_score_answer_valid(score: float, min_s: float, max_s: float) -> None:
    """Verifica o comportamento de score answer valid."""
    ans = ScoreAnswer(score=score, min_score=min_s, max_score=max_s, confidence=0.88)
    assert ans.type == "score"
    assert ans.score == score


@pytest.mark.parametrize(
    ("score", "min_s", "max_s"),
    [
        (-0.1, 0.0, 1.0),
        (1.1, 0.0, 1.0),
        (0.5, 1.0, 5.0),
        (5.5, 1.0, 5.0),
        (float("nan"), 0.0, 1.0),
        (float("inf"), 0.0, 1.0),
    ],
)
def test_score_answer_out_of_bounds(score: float, min_s: float, max_s: float) -> None:
    """Verifica o comportamento de score answer out of bounds."""
    with pytest.raises(ValidationError):
        ScoreAnswer(score=score, min_score=min_s, max_score=max_s)


# ---------------------------------------------------------------------------
# 4. ClefLocalAdapter Matrix & Error Handling (~35 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "expected_exc"),
    [
        (400, SystemOneValidationError),
        (401, SystemOneAuthenticationError),
        (403, SystemOneAuthenticationError),
        (404, SystemOneCapabilityError),
        (429, SystemOneRateLimitError),
        (500, SystemOneConnectionError),
        (502, SystemOneConnectionError),
        (503, SystemOneConnectionError),
        (504, SystemOneConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_clef_local_http_status_codes(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_exc: type[Exception]
) -> None:
    """Verifica o comportamento de clef local http status codes."""
    adapter = ClefLocalAdapter(base_url="http://127.0.0.1:8080")
    req = SystemOneRequest(
        model="clef-flash",
        state="Teste",
        questions={"q": NoulQuestion(criterion="crit")},
    )

    async def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        """Executa a operação fake post."""
        return httpx.Response(status_code, text="Erro simulado")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(expected_exc):
        await adapter.decide(req)


@pytest.mark.parametrize(
    ("client_error", "expected_exc"),
    [
        (httpx.ConnectError("Connection refused"), SystemOneConnectionError),
        (httpx.TimeoutException("Read timeout"), SystemOneTimeoutError),
        (httpx.NetworkError("Network down"), SystemOneConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_clef_local_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    client_error: Exception,
    expected_exc: type[Exception],
) -> None:
    """Verifica o comportamento de clef local network errors."""
    adapter = ClefLocalAdapter(base_url="http://127.0.0.1:8080")
    req = SystemOneRequest(
        model="clef-flash",
        state="Teste",
        questions={"q": NoulQuestion(criterion="crit")},
    )

    async def fake_post_err(*args: object, **kwargs: object) -> httpx.Response:
        """Executa a operação fake post err."""
        raise client_error

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post_err)
    with pytest.raises(expected_exc):
        await adapter.decide(req)


@pytest.mark.asyncio
async def test_clef_local_capabilities() -> None:
    """Verifica o comportamento de clef local capabilities."""
    adapter = ClefLocalAdapter()
    caps = await adapter.capabilities()
    assert caps.supports_noul is True
    assert caps.supports_choice is True
    assert caps.supports_score is True
    assert caps.supports_image is False


@pytest.mark.parametrize(
    ("health_status", "is_ok"), [(200, True), (500, False), (404, False)]
)
@pytest.mark.asyncio
async def test_clef_local_health(
    monkeypatch: pytest.MonkeyPatch, health_status: int, is_ok: bool
) -> None:
    """Verifica o comportamento de clef local health."""
    adapter = ClefLocalAdapter(base_url="http://127.0.0.1:8080")

    async def fake_get(*args: object, **kwargs: object) -> httpx.Response:
        """Executa a operação fake get."""
        return httpx.Response(health_status, json={"status": "ok"} if is_ok else {})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await adapter.health() is is_ok


# ---------------------------------------------------------------------------
# 5. ClefApiAdapter & JevApiAdapter Matrix Tests (~30 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("api_key", ["", "   "])
@pytest.mark.asyncio
async def test_api_adapters_empty_keys(api_key: str) -> None:
    """Verifica o comportamento de api adapters empty keys."""
    clef = ClefApiAdapter(api_key=api_key, account_id="test-account")
    jev = JevApiAdapter(api_key=api_key)
    req = SystemOneRequest(
        model="clef-flash",
        state="Teste",
        questions={"q": NoulQuestion(criterion="crit")},
    )
    with pytest.raises(SystemOneAuthenticationError):
        await clef.decide(req)
    with pytest.raises(SystemOneAuthenticationError):
        await jev.decide(req)


@pytest.mark.asyncio
async def test_clef_api_capabilities_supports_images() -> None:
    """Verifica o comportamento de clef api capabilities supports images."""
    clef = ClefApiAdapter(api_key="valid_key", account_id="test-account")
    caps = await clef.capabilities()
    assert caps.supports_image is True


@pytest.mark.asyncio
async def test_jev_api_capabilities_supports_images() -> None:
    """Verifica o comportamento de jev api capabilities supports images."""
    jev = JevApiAdapter(api_key="valid_key")
    caps = await jev.capabilities()
    assert caps.supports_image is True


@pytest.mark.parametrize(
    ("status_code", "expected_exc"),
    [
        (401, SystemOneAuthenticationError),
        (403, SystemOneAuthenticationError),
        (429, SystemOneRateLimitError),
        (500, SystemOneConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_clef_api_http_errors(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_exc: type[Exception]
) -> None:
    """Verifica o comportamento de clef api http errors."""
    clef = ClefApiAdapter(api_key="test_token", account_id="test-account")
    req = SystemOneRequest(
        model="@cf/cloudflare/clef-flash",
        state="State",
        questions={"q": NoulQuestion(criterion="c")},
    )

    async def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        """Executa a operação fake post."""
        return httpx.Response(status_code, text="Erro")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(expected_exc):
        await clef.decide(req)


@pytest.mark.parametrize(
    ("status_code", "expected_exc"),
    [
        (401, SystemOneAuthenticationError),
        (403, SystemOneAuthenticationError),
        (429, SystemOneRateLimitError),
        (500, SystemOneConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_jev_api_http_errors(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_exc: type[Exception]
) -> None:
    """Verifica o comportamento de jev api http errors."""
    jev = JevApiAdapter(api_key="test_token")
    req = SystemOneRequest(
        model="jev-fast",
        state="State",
        questions={"q": NoulQuestion(criterion="c")},
    )

    async def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        """Executa a operação fake post."""
        return httpx.Response(status_code, text="Erro")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(expected_exc):
        await jev.decide(req)


# ---------------------------------------------------------------------------
# 6. DecisionPolicyEngine Policy & Safety Matrix (~35 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_high_impact", [True, False])
@pytest.mark.parametrize("confidence", [0.4, 0.6, 0.79, 0.81, 0.95, 1.0])
def test_policy_high_impact_and_confidence_matrix(
    is_high_impact: bool, confidence: float
) -> None:
    """Verifica o comportamento de policy high impact and confidence matrix."""
    settings = SystemOneSettings(enabled=True, confidence_threshold_hitl=0.80)
    engine = DecisionPolicyEngine(settings)

    resp = SystemOneResponse(
        answers={"q1": NoulAnswer(probability=0.9, confidence=confidence)},
        metadata=SystemOneMetadata(
            provider="clef-local", model="clef-flash", latency_ms=5.0
        ),
    )

    result = engine.evaluate_response(resp, is_high_impact=is_high_impact)
    if is_high_impact:
        assert result.outcome == "requires_hitl"
        assert result.requires_hitl is True
    elif confidence < 0.80:
        assert result.outcome == "requires_hitl"
    else:
        assert result.outcome == "accepted"
        assert result.requires_hitl is False


@pytest.mark.parametrize(
    ("d1", "d2", "should_hitl"),
    [
        (0.50, 0.50, True),
        (0.51, 0.49, True),
        (0.52, 0.48, True),
        (0.55, 0.45, False),
        (0.70, 0.30, False),
        (0.90, 0.10, False),
    ],
)
def test_policy_choice_tie_breaker(d1: float, d2: float, should_hitl: bool) -> None:
    """Verifica o comportamento de policy choice tie breaker."""
    settings = SystemOneSettings(enabled=True, confidence_threshold_hitl=0.70)
    engine = DecisionPolicyEngine(settings)

    resp = SystemOneResponse(
        answers={
            "q1": ChoiceAnswer(
                selection="opt1",
                distribution={"opt1": d1, "opt2": d2},
                confidence=0.95,
            )
        },
        metadata=SystemOneMetadata(
            provider="clef-local", model="clef-flash", latency_ms=5.0
        ),
    )

    result = engine.evaluate_response(resp, is_high_impact=False)
    if should_hitl:
        assert result.outcome == "requires_hitl"
        assert "Empate" in result.reason
    else:
        assert result.outcome == "accepted"


# ---------------------------------------------------------------------------
# 7. Endpoints & Settings Matrix Tests (~30 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        None,
        "",
        "123",
        "shortkey",
        "cf_abcdef123456789",
        "jev_live_api_key_9988776655",
    ],
)
def test_mask_secret_permutations(secret: str | None) -> None:
    """Verifica o comportamento de mask secret permutations."""
    res = mask_secret(secret)
    if not secret:
        assert res is None
    else:
        assert res is not None
        assert res.startswith("********#")
        assert len(res) == len("********#") + 8
        assert secret[-3:] not in res


@pytest.mark.parametrize("provider_name", ["clef-local", "clef-api", "jev-api"])
def test_get_systemone_provider_matrix(provider_name: DecisionProviderType) -> None:
    """Verifica o comportamento de get systemone provider matrix."""
    settings = SystemOneSettings(
        provider=provider_name,
        clef_account_id="account-test" if provider_name == "clef-api" else "",
    )
    prov = get_systemone_provider(
        settings,
        clef_api_key="cf_mock_key",
        jev_api_key="jev_mock_key",
    )
    assert prov.provider_id == provider_name


@pytest.mark.parametrize("threshold", [0.0, 0.1, 0.5, 0.8, 0.95, 1.0])
def test_settings_threshold_validation(threshold: float) -> None:
    """Verifica o comportamento de settings threshold validation."""
    s = SystemOneSettings(confidence_threshold_hitl=threshold)
    assert s.confidence_threshold_hitl == threshold


@pytest.mark.parametrize("invalid_threshold", [-0.1, 1.05, 5.0])
def test_settings_invalid_threshold(invalid_threshold: float) -> None:
    """Verifica o comportamento de settings invalid threshold."""
    with pytest.raises(ValidationError):
        SystemOneSettings(confidence_threshold_hitl=invalid_threshold)


# ---------------------------------------------------------------------------
# 8. Endpoints HTTP (/decision-models/status e /decision-models/config) Matrix (~60 testes)
# ---------------------------------------------------------------------------

from backend.api.handlers.provider_routing import (
    SystemOneStatusResponse,
    UpdateSystemOneConfigRequest,
    get_decision_models_status,
    update_decision_models_config,
)
from backend.settings import settings as app_settings

_SYSTEMONE_SETTING_ATTRS = (
    "systemone_enabled",
    "systemone_provider",
    "systemone_local_base_url",
    "systemone_local_model",
    "systemone_laya_base_url",
    "systemone_laya_model",
    "systemone_laya_api_key",
    "systemone_clef_api_base_url",
    "systemone_clef_account_id",
    "systemone_clef_api_model",
    "systemone_jev_api_base_url",
    "systemone_jev_api_model",
    "systemone_allow_remote_fallback",
    "systemone_confidence_threshold_hitl",
    "clef_api_key",
    "jev_api_key",
)


@pytest.fixture(autouse=True)
def _isolate_systemone_app_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executa a operação isolate systemone app settings."""
    for attr in _SYSTEMONE_SETTING_ATTRS:
        if hasattr(app_settings, attr):
            monkeypatch.setattr(app_settings, attr, getattr(app_settings, attr))

    async def healthy(_: object) -> bool:
        """Mantém os testes de configuração independentes de serviços externos."""
        return True

    monkeypatch.setattr(BaseSystemOneApiAdapter, "health", healthy)
    monkeypatch.setattr(LayaLocalAdapter, "health", healthy)


@pytest.mark.asyncio
async def test_get_decision_models_status_structure() -> None:
    """Verifica o comportamento de get decision models status structure."""
    res = await get_decision_models_status()
    assert isinstance(res, SystemOneStatusResponse)
    assert res.provider in {"clef-local", "laya-local", "clef-api", "jev-api"}
    assert isinstance(res.is_healthy, bool)


@pytest.mark.parametrize("prov", ["clef-local", "laya-local", "clef-api", "jev-api"])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.asyncio
async def test_update_decision_models_config_provider_and_enabled(
    prov: DecisionProviderType, enabled: bool
) -> None:
    """Verifica o comportamento de update decision models config provider and enabled."""
    req = UpdateSystemOneConfigRequest(provider=prov, enabled=enabled)
    res = await update_decision_models_config(req, _=None)
    assert res.provider == prov
    assert res.enabled == enabled


@pytest.mark.parametrize("hitl_thresh", [0.0, 0.25, 0.5, 0.75, 0.85, 0.99])
@pytest.mark.asyncio
async def test_update_decision_models_config_hitl_threshold(hitl_thresh: float) -> None:
    """Verifica o comportamento de update decision models config hitl threshold."""
    req = UpdateSystemOneConfigRequest(confidence_threshold_hitl=hitl_thresh)
    res = await update_decision_models_config(req, _=None)
    assert res.confidence_threshold_hitl == hitl_thresh


@pytest.mark.parametrize("fallback", [True, False])
@pytest.mark.asyncio
async def test_update_decision_models_config_remote_fallback(fallback: bool) -> None:
    """Verifica o comportamento de update decision models config remote fallback."""
    req = UpdateSystemOneConfigRequest(allow_remote_fallback=fallback)
    res = await update_decision_models_config(req, _=None)
    assert res.allow_remote_fallback == fallback


@pytest.mark.parametrize(
    "local_url",
    [
        "http://127.0.0.1:8080",
        "http://localhost:18080",
        "http://192.168.1.100:8000",
    ],
)
@pytest.mark.asyncio
async def test_update_decision_models_config_local_urls(local_url: str) -> None:
    """Verifica o comportamento de update decision models config local urls."""
    req = UpdateSystemOneConfigRequest(local_base_url=local_url)
    res = await update_decision_models_config(req, _=None)
    assert res.local_base_url == local_url


@pytest.mark.asyncio
async def test_update_decision_models_config_persists_changed_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toda alteração de configuração deve sobreviver a um novo processo."""
    from backend.api.handlers import provider_routing

    env_file = tmp_path / ".env"
    monkeypatch.setattr(provider_routing, "_env_file", lambda: env_file)
    res = await provider_routing.update_decision_models_config(
        UpdateSystemOneConfigRequest(
            local_base_url="http://127.0.0.1:9999",
            local_model="decision-test",
            laya_base_url="http://127.0.0.1:11434",
            laya_model="laya-test",
            clef_account_id="account-test",
            confidence_threshold_hitl=0.7,
        ),
        _=None,
    )

    saved = env_file.read_text(encoding="utf-8")
    assert "SYSTEMONE_LOCAL_BASE_URL=http://127.0.0.1:9999" in saved
    assert "SYSTEMONE_LOCAL_MODEL=decision-test" in saved
    assert "SYSTEMONE_LAYA_MODEL=laya-test" in saved
    assert "SYSTEMONE_CLEF_ACCOUNT_ID=account-test" in saved
    assert "SYSTEMONE_CONFIDENCE_THRESHOLD_HITL=0.7" in saved
    assert res.clef_account_id == "account-test"


@pytest.mark.parametrize(
    ("key_input", "expected_configured"),
    [
        ("cf_secret_key_12345", True),
        ("", False),
        ("   ", False),
    ],
)
@pytest.mark.asyncio
async def test_update_decision_models_clef_key(
    key_input: str, expected_configured: bool
) -> None:
    """Verifica o comportamento de update decision models clef key."""
    req = UpdateSystemOneConfigRequest(clef_api_key=key_input)
    res = await update_decision_models_config(req, _=None)
    assert res.clef_api_key_configured is expected_configured


@pytest.mark.parametrize(
    ("key_input", "expected_configured"),
    [
        ("jev_secret_key_12345", True),
        ("", False),
        ("   ", False),
    ],
)
@pytest.mark.asyncio
async def test_update_decision_models_jev_key(
    key_input: str, expected_configured: bool
) -> None:
    """Verifica o comportamento de update decision models jev key."""
    req = UpdateSystemOneConfigRequest(jev_api_key=key_input)
    res = await update_decision_models_config(req, _=None)
    assert res.jev_api_key_configured is expected_configured


# ---------------------------------------------------------------------------
# 9. Request / Response Schemas & Permutations (~40 testes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_name",
    [
        "clef-flash",
        "@cf/cloudflare/clef-flash",
        "jev-fast",
        "custom-decision-v1",
    ],
)
@pytest.mark.parametrize("state_len", [10, 100, 1000])
def test_systemone_request_construction(model_name: str, state_len: int) -> None:
    """Verifica o comportamento de systemone request construction."""
    state_str = "x" * state_len
    req = SystemOneRequest(
        model=model_name,
        state=state_str,
        questions={
            "q1": NoulQuestion(criterion="critério"),
            "q2": ChoiceQuestion(instructions="escolha", options=["a", "b"]),
            "q3": ScoreQuestion(criterion="qualidade", min_score=0.0, max_score=10.0),
        },
    )
    assert req.model == model_name
    assert len(req.state) == state_len
    assert len(req.questions) == 3


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens"),
    [
        (0, 0),
        (10, 5),
        (500, 20),
        (10000, 50),
    ],
)
def test_systemone_usage_computation(
    prompt_tokens: int, completion_tokens: int
) -> None:
    """Verifica o comportamento de systemone usage computation."""
    usage = SystemOneUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    assert usage.prompt_tokens == prompt_tokens
    assert usage.completion_tokens == completion_tokens
    assert usage.total_tokens == prompt_tokens + completion_tokens
