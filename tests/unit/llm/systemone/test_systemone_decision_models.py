"""Testes unitários e de contrato para SystemOne / Decision Models (JEV e Clef)."""

from __future__ import annotations

import inspect

import httpx
import pytest
from pydantic import ValidationError

from backend.llm.systemone.api_adapters import ClefApiAdapter, JevApiAdapter
from backend.llm.systemone.clef_local import ClefLocalAdapter
from backend.llm.systemone.errors import (
    SystemOneAuthenticationError,
    SystemOneCapabilityError,
    SystemOneConnectionError,
    SystemOneRateLimitError,
    SystemOneTimeoutError,
    SystemOneValidationError,
)
from backend.llm.systemone.policy import DecisionPolicyEngine
from backend.llm.systemone.settings import (
    SystemOneSettings,
    get_systemone_provider,
    mask_secret,
)
from backend.llm.systemone.types import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneMetadata,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["clef-api", "jev-api"])
async def test_remote_health_distinguishes_invalid_credential(
    provider: str,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer invalid-key"
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = (
            ClefApiAdapter(api_key="invalid-key", account_id="account", client=client)
            if provider == "clef-api"
            else JevApiAdapter(api_key="invalid-key", client=client)
        )
        with pytest.raises(SystemOneAuthenticationError):
            await adapter.health()


class TestSystemOneTypesValidation:
    """Validação de schemas Pydantic de tipos canônicos (Noul, Choice, Score)."""

    def test_noul_question_and_answer_valid(self) -> None:
        """Verifica o comportamento de noul question and answer valid."""
        q = NoulQuestion(
            criterion="A solicitação envolve exclusão permanente de dados?"
        )
        assert q.type == "noul"

        ans = NoulAnswer(probability=0.85, confidence=0.92)
        assert ans.type == "noul"
        assert ans.probability == 0.85
        assert ans.confidence == 0.92

    def test_noul_answer_bounds_and_finite(self) -> None:
        """Verifica o comportamento de noul answer bounds and finite."""
        with pytest.raises(ValidationError):
            NoulAnswer(probability=1.5)

        with pytest.raises(ValidationError):
            NoulAnswer(probability=-0.1)

    def test_choice_question_options_integrity(self) -> None:
        # Pelo menos 2 opções únicas
        """Verifica o comportamento de choice question options integrity."""
        q = ChoiceQuestion(
            instructions="Escolha a categoria",
            options=["code", "architecture", "security"],
        )
        assert len(q.options) == 3

        with pytest.raises(ValidationError):
            # Apenas 1 opção deve falhar
            ChoiceQuestion(options=["code"])

        with pytest.raises(ValidationError):
            # Opções duplicadas devem falhar
            ChoiceQuestion(options=["code", "code"])

        with pytest.raises(ValidationError):
            # Opção vazia deve falhar
            ChoiceQuestion(options=["code", "   "])

    def test_choice_answer_distribution_bounds(self) -> None:
        """Verifica o comportamento de choice answer distribution bounds."""
        ans = ChoiceAnswer(
            selection="code",
            distribution={"code": 0.8, "architecture": 0.2},
            confidence=0.85,
        )
        assert ans.selection == "code"

        with pytest.raises(ValidationError):
            ChoiceAnswer(
                selection="code",
                distribution={"code": 1.5, "architecture": -0.5},
            )

    def test_score_question_and_answer_bounds(self) -> None:
        """Verifica o comportamento de score question and answer bounds."""
        q = ScoreQuestion(
            criterion="Severidade de risco da alteração",
            min_score=1.0,
            max_score=5.0,
        )
        assert q.min_score == 1.0
        assert q.max_score == 5.0

        ans = ScoreAnswer(score=4.5, confidence=0.9)
        assert ans.score == 4.5

        with pytest.raises(ValidationError):
            # min_score >= max_score deve falhar
            ScoreQuestion(criterion="Teste", min_score=5.0, max_score=1.0)


class TestClefLocalAdapter:
    """Testes de contrato para o adapter Clef local (llama.cpp POST /v1/systemone)."""

    @pytest.mark.asyncio
    async def test_clef_local_decide_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de clef local decide success."""
        adapter = ClefLocalAdapter(base_url="http://127.0.0.1:8080")

        req = SystemOneRequest(
            model="clef-flash",
            state="O usuário pediu para rodar git reset --hard HEAD~1",
            questions={
                "q1": NoulQuestion(
                    criterion="Essa ação é destrutiva para o repositório?"
                ),
                "q2": ChoiceQuestion(
                    instructions="Classifique o risco",
                    options=["low", "medium", "high"],
                ),
                "q3": ScoreQuestion(
                    criterion="Severidade do impacto de 1 a 10",
                    min_score=1.0,
                    max_score=10.0,
                ),
            },
        )

        mock_response_json = {
            "answers": {
                "q1": {"type": "noul", "probability": 0.95, "confidence": 0.98},
                "q2": {
                    "type": "choice",
                    "selection": "high",
                    "distribution": {"low": 0.01, "medium": 0.04, "high": 0.95},
                    "confidence": 0.95,
                },
                "q3": {"type": "score", "score": 9.0, "confidence": 0.90},
            },
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 15,
                "total_tokens": 135,
            },
        }

        async def fake_post(
            client: httpx.AsyncClient, url: str, **kwargs: object
        ) -> httpx.Response:
            """Executa a operação fake post."""
            assert url == "http://127.0.0.1:8080/v1/systemone"
            return httpx.Response(200, json=mock_response_json)

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        resp = await adapter.decide(req)
        assert isinstance(resp, SystemOneResponse)
        assert len(resp.answers) == 3
        assert isinstance(resp.answers["q1"], NoulAnswer)
        assert resp.answers["q1"].probability == 0.95
        assert isinstance(resp.answers["q2"], ChoiceAnswer)
        assert resp.answers["q2"].selection == "high"
        assert isinstance(resp.answers["q3"], ScoreAnswer)
        assert resp.answers["q3"].score == 9.0
        assert resp.metadata.usage.total_tokens == 135

    @pytest.mark.asyncio
    async def test_clef_local_rejects_unsupported_media(self) -> None:
        """Verifica o comportamento de clef local rejects unsupported media."""
        adapter = ClefLocalAdapter()
        req = SystemOneRequest(
            model="clef-flash",
            state="Analise a imagem",
            media_urls=["file://test.png"],
            questions={"q1": NoulQuestion(criterion="Imagem corrompida?")},
        )
        with pytest.raises(SystemOneCapabilityError):
            await adapter.decide(req)

    @pytest.mark.asyncio
    async def test_clef_local_handles_http_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de clef local handles http errors."""
        adapter = ClefLocalAdapter()
        req = SystemOneRequest(
            model="clef-flash",
            state="Context",
            questions={"q1": NoulQuestion(criterion="Critério")},
        )

        async def fake_429(*args: object, **kwargs: object) -> httpx.Response:
            """Executa a operação fake 429."""
            return httpx.Response(429, text="Rate limit exceeded")

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_429)
        with pytest.raises(SystemOneRateLimitError):
            await adapter.decide(req)

        async def fake_timeout(*args: object, **kwargs: object) -> httpx.Response:
            """Executa a operação fake timeout."""
            raise httpx.TimeoutException("Timeout")

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_timeout)
        with pytest.raises(SystemOneTimeoutError):
            await adapter.decide(req)

        async def fake_not_implemented(
            *args: object, **kwargs: object
        ) -> httpx.Response:
            """Simula um servidor que não implementa decisões estruturadas."""
            return httpx.Response(501, text="systemone not implemented")

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_not_implemented)
        with pytest.raises(SystemOneConnectionError):
            await adapter.decide(req)


class TestApiAdapters:
    """Testes de contrato para os adapters JEV API e Clef API."""

    def test_clef_api_adapter_requires_account_id(self) -> None:
        """Verifica o comportamento de clef api adapter requires account id."""
        parameter = inspect.signature(ClefApiAdapter.__init__).parameters["account_id"]
        assert parameter.default is inspect.Parameter.empty
        with pytest.raises(ValueError, match="account_id é obrigatório"):
            ClefApiAdapter(api_key="key", account_id="")

    @pytest.mark.asyncio
    async def test_clef_api_adapter_requires_key(self) -> None:
        """Verifica o comportamento de clef api adapter requires key."""
        adapter = ClefApiAdapter(api_key="", account_id="test-account")
        req = SystemOneRequest(
            model="@cf/cloudflare/clef-flash",
            state="State",
            questions={"q": NoulQuestion(criterion="crit")},
        )
        with pytest.raises(SystemOneAuthenticationError):
            await adapter.decide(req)

    @pytest.mark.asyncio
    async def test_clef_api_uses_requested_model_in_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de clef api uses requested model in endpoint."""
        adapter = ClefApiAdapter(api_key="key", account_id="account-test")
        req = SystemOneRequest(
            model="@cf/custom/clef-test",
            state="State",
            questions={"q": NoulQuestion(criterion="crit")},
        )
        payload = {
            "answers": {"q": {"type": "noul", "probability": 0.9, "confidence": 0.9}},
            "usage": {"total_tokens": 1},
        }
        seen: dict[str, str] = {}

        async def fake_post(
            client: httpx.AsyncClient, url: str, **kwargs: object
        ) -> httpx.Response:
            """Executa a operação fake post."""
            seen["url"] = url
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        await adapter.decide(req)
        assert seen["url"].endswith("/account-test/ai/run/@cf/custom/clef-test")

    @pytest.mark.asyncio
    async def test_clef_api_unwraps_cloudflare_success_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aceita o envelope ``success/result`` retornado pela API Cloudflare."""
        adapter = ClefApiAdapter(api_key="key", account_id="account-test")
        req = SystemOneRequest(
            model="@cf/cloudflare/clef-flash",
            state="State",
            questions={"q": NoulQuestion(criterion="crit")},
        )

        async def fake_post(
            client: httpx.AsyncClient, url: str, **kwargs: object
        ) -> httpx.Response:
            """Executa a operação fake post."""
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "answers": {
                            "q": {
                                "type": "noul",
                                "probability": 0.8,
                                "confidence": 0.9,
                            }
                        },
                        "usage": {"total_tokens": 2},
                    },
                },
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        result = await adapter.decide(req)
        answer = result.answers["q"]
        assert isinstance(answer, NoulAnswer)
        assert answer.probability == 0.8

    @pytest.mark.asyncio
    async def test_clef_api_rejects_failed_cloudflare_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Não trata ``success=false`` como uma resposta de decisão válida."""
        adapter = ClefApiAdapter(api_key="key", account_id="account-test")
        req = SystemOneRequest(
            model="@cf/cloudflare/clef-flash",
            state="State",
            questions={"q": NoulQuestion(criterion="crit")},
        )

        async def fake_post(
            client: httpx.AsyncClient, url: str, **kwargs: object
        ) -> httpx.Response:
            """Executa a operação fake post."""
            return httpx.Response(200, json={"success": False, "result": None})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with pytest.raises(SystemOneValidationError):
            await adapter.decide(req)

    @pytest.mark.asyncio
    async def test_jev_api_adapter_decide_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verifica o comportamento de jev api adapter decide success."""
        adapter = JevApiAdapter(api_key="jev_secret_test_key")
        req = SystemOneRequest(
            model="jev-fast",
            state="State de teste",
            questions={"q1": NoulQuestion(criterion="critério de teste")},
        )

        mock_payload = {
            "answers": {
                "q1": {"type": "noul", "probability": 0.88, "confidence": 0.91},
            },
            "usage": {"total_tokens": 50},
        }

        async def fake_post(
            client: httpx.AsyncClient, url: str, **kwargs: object
        ) -> httpx.Response:
            """Executa a operação fake post."""
            headers = kwargs.get("headers", {})
            assert isinstance(headers, dict)
            assert headers.get("Authorization") == "Bearer jev_secret_test_key"
            return httpx.Response(200, json=mock_payload)

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        res = await adapter.decide(req)
        assert res.answers["q1"].type == "noul"
        assert isinstance(res.answers["q1"], NoulAnswer)
        assert res.answers["q1"].probability == 0.88


class TestDecisionPolicyEngine:
    """Testes da policy de segurança, guardrails e encaminhamento para HITL."""

    @pytest.mark.asyncio
    async def test_high_impact_always_requires_hitl(self) -> None:
        """Verifica o comportamento de high impact always requires hitl."""
        settings = SystemOneSettings(enabled=True)
        engine = DecisionPolicyEngine(settings)

        resp = SystemOneResponse(
            answers={"q1": NoulAnswer(probability=0.99, confidence=0.99)},
            metadata=SystemOneMetadata(
                provider="clef-local",
                model="clef-flash",
                latency_ms=10.0,
            ),
        )

        eval_result = engine.evaluate_response(resp, is_high_impact=True)
        assert eval_result.outcome == "requires_hitl"
        assert eval_result.requires_hitl is True
        assert "alto impacto" in eval_result.reason

    @pytest.mark.asyncio
    async def test_low_confidence_requires_hitl(self) -> None:
        """Verifica o comportamento de low confidence requires hitl."""
        settings = SystemOneSettings(enabled=True, confidence_threshold_hitl=0.80)
        engine = DecisionPolicyEngine(settings)

        resp = SystemOneResponse(
            answers={"q1": NoulAnswer(probability=0.90, confidence=0.65)},
            metadata=SystemOneMetadata(
                provider="clef-local",
                model="clef-flash",
                latency_ms=10.0,
            ),
        )

        eval_result = engine.evaluate_response(resp, is_high_impact=False)
        assert eval_result.outcome == "requires_hitl"
        assert "abaixo do limiar de segurança" in eval_result.reason

    @pytest.mark.asyncio
    async def test_choice_tie_triggers_hitl(self) -> None:
        """Verifica o comportamento de choice tie triggers hitl."""
        settings = SystemOneSettings(enabled=True)
        engine = DecisionPolicyEngine(settings)

        resp = SystemOneResponse(
            answers={
                "q1": ChoiceAnswer(
                    selection="opt1",
                    distribution={"opt1": 0.49, "opt2": 0.48, "opt3": 0.03},
                    confidence=0.90,
                )
            },
            metadata=SystemOneMetadata(
                provider="clef-local",
                model="clef-flash",
                latency_ms=10.0,
            ),
        )

        eval_result = engine.evaluate_response(resp, is_high_impact=False)
        assert eval_result.outcome == "requires_hitl"
        assert "Empate ou ambiguidade" in eval_result.reason

    @pytest.mark.asyncio
    async def test_remote_fallback_policy_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fallback desabilitado por padrão
        """Verifica o comportamento de remote fallback policy disabled by default."""
        settings = SystemOneSettings(enabled=True, allow_remote_fallback=False)
        engine = DecisionPolicyEngine(settings)

        async def fake_post(*args: object, **kwargs: object) -> httpx.Response:
            """Executa a operação fake post."""
            raise httpx.ConnectError("Local provider down")

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        primary = ClefLocalAdapter()
        fallback = ClefApiAdapter(api_key="key", account_id="test-account")

        req = SystemOneRequest(
            model="clef-flash",
            state="state",
            questions={"q1": NoulQuestion(criterion="crit")},
        )

        result = await engine.execute_decision(
            primary,
            req,
            fallback_provider=fallback,
            is_high_impact=False,
        )
        # Como o primário falha (sem servidor) e o fallback está desabilitado, deve acionar HITL sem chamar remoto
        assert result.outcome == "requires_hitl"
        assert "Falha no provedor de decisão" in result.reason


class TestSettingsAndSecrets:
    """Testes de configurações e mascaramento de credenciais."""

    def test_mask_secret(self) -> None:
        """Verifica o comportamento de mask secret."""
        assert mask_secret(None) is None
        short_mask = mask_secret("short")
        assert short_mask is not None
        assert short_mask.startswith("********#")
        long_mask = mask_secret("vst_secret_key_123456789")
        assert long_mask is not None
        assert long_mask.startswith("********#")
        assert "vst" not in long_mask
        assert "6789" not in long_mask

    def test_provider_factory(self) -> None:
        """Verifica o comportamento de provider factory."""
        s_local = SystemOneSettings(provider="clef-local")
        p_local = get_systemone_provider(s_local)
        assert p_local.provider_id == "clef-local"

        s_clef = SystemOneSettings(provider="clef-api", clef_account_id="account-test")
        p_clef = get_systemone_provider(s_clef, clef_api_key="cf_key")
        assert p_clef.provider_id == "clef-api"

        s_jev = SystemOneSettings(provider="jev-api")
        p_jev = get_systemone_provider(s_jev, jev_api_key="jev_key")
        assert p_jev.provider_id == "jev-api"

    @pytest.mark.asyncio
    async def test_local_provider_advertises_configured_model(self) -> None:
        """Verifica o comportamento de local provider advertises configured model."""
        settings = SystemOneSettings(
            provider="clef-local", local_model="qwen-custom.gguf"
        )
        provider = get_systemone_provider(settings)
        capabilities = await provider.capabilities()
        assert "qwen-custom.gguf" in capabilities.supported_models

    @pytest.mark.asyncio
    async def test_local_provider_rejects_empty_model_configuration(self) -> None:
        """Verifica o comportamento de local provider rejects empty model configuration."""
        adapter = ClefLocalAdapter(model="")
        capabilities = await adapter.capabilities()
        assert "" not in capabilities.supported_models
