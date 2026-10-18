"""Fallback multi-provider de LLM (Parte A).

Contrato:
- is_quota_error: reconhece 429/quota/rate-limit; ignora outros erros.
- get_fallback_chain: lê llm_fallback_order, remove o provider atual e os sem key.
- try_with_fallback: tenta o modelo; em quota error percorre a cadeia; em troca
  registra record_switch + on_switch; esgotou tudo → QuotaExhaustedError.
- record_switch/drain_switches: fila por ContextVar, drena e limpa.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.llm import provider_fallback as pf

# ---------------------------------------------------------------------------
# is_quota_error
# ---------------------------------------------------------------------------


class TestIsQuotaError:
    def test_429(self):
        assert pf.is_quota_error(Exception("429 RESOURCE_EXHAUSTED")) is True

    def test_resource_exhausted(self):
        assert pf.is_quota_error(Exception("RESOURCE_EXHAUSTED")) is True

    def test_quota_word(self):
        assert pf.is_quota_error(Exception("You exceeded your current quota")) is True

    def test_rate_limit_space(self):
        assert pf.is_quota_error(Exception("Rate limit reached")) is True

    def test_rate_limit_underscore(self):
        assert pf.is_quota_error(Exception("rate_limit_exceeded")) is True

    def test_case_insensitive(self):
        assert pf.is_quota_error(Exception("Quota Exceeded")) is True

    def test_non_quota_connection(self):
        assert pf.is_quota_error(Exception("connection refused")) is False

    def test_non_quota_value_error(self):
        assert pf.is_quota_error(ValueError("bad input")) is False

    def test_openrouter_rate_limit_error_reconhecida_por_tipo(self):
        """Reconhece por `isinstance`, não por substring — mensagem sem
        nenhum marker de `_QUOTA_MARKERS` ainda é detectada como quota."""
        from backend.llm.openrouter.client import OpenRouterRateLimitError

        exc = OpenRouterRateLimitError("too many requests, slow down")
        assert pf.is_quota_error(exc) is True

    def test_generic_exception_com_texto_de_rate_limit_ainda_funciona(self):
        """Regressão: exceção não tipada (Anthropic/Gemini/Cohere levantam a
        exceção genérica da própria SDK) continua reconhecida via substring."""
        assert pf.is_quota_error(Exception("429 rate limit exceeded")) is True

    def test_empty(self):
        assert pf.is_quota_error(Exception("")) is False

    def test_quota_exhausted_error_is_quota(self):
        assert pf.is_quota_error(pf.QuotaExhaustedError("gemini esgotou")) is True


# ---------------------------------------------------------------------------
# is_transient_error
# ---------------------------------------------------------------------------


class TestIsTransientError:
    def test_timeout_word(self):
        assert pf.is_transient_error(Exception("ReadTimeout: timed out")) is True

    def test_timed_out(self):
        assert pf.is_transient_error(Exception("request timed out after 30s")) is True

    def test_connecttimeout(self):
        assert (
            pf.is_transient_error(Exception("ConnectTimeout connecting to API")) is True
        )

    def test_readtimeout(self):
        assert pf.is_transient_error(Exception("ReadTimeout reading response")) is True

    def test_connection_error(self):
        assert (
            pf.is_transient_error(Exception("connection error: reset by peer")) is True
        )

    def test_connection_refused(self):
        assert pf.is_transient_error(Exception("connection refused")) is True

    def test_case_insensitive(self):
        assert pf.is_transient_error(Exception("TIMEOUT waiting for model")) is True

    def test_gemini_503_high_demand_real(self):
        # Texto real de um ServerError do google-genai (visto em produção).
        msg = (
            "503 Service Unavailable. {'message': '{\\n  \"error\": {\\n    "
            '"code": 503,\\n    "message": "This model is currently '
            "experiencing high demand. Spikes in demand are usually "
            'temporary. Please try again later.",\\n    "status": '
            "\"UNAVAILABLE\"\\n  }\\n}\\n', 'status': 'Service Unavailable'}"
        )
        assert pf.is_transient_error(Exception(msg)) is True

    def test_anthropic_overloaded(self):
        assert pf.is_transient_error(Exception("overloaded_error: overloaded")) is True

    def test_service_unavailable(self):
        assert pf.is_transient_error(Exception("503 service unavailable")) is True

    # erros que NÃO são transientes
    def test_quota_not_transient(self):
        assert pf.is_transient_error(Exception("429 quota exceeded")) is False

    def test_auth_not_transient(self):
        assert pf.is_transient_error(Exception("401 Unauthorized")) is False

    def test_value_error_not_transient(self):
        assert pf.is_transient_error(ValueError("bad input")) is False

    def test_empty_not_transient(self):
        assert pf.is_transient_error(Exception("")) is False


# ---------------------------------------------------------------------------
# is_provider_incompatible_error
# ---------------------------------------------------------------------------


class TestIsProviderIncompatibleError:
    def test_cohere_tool_plan_rejected(self):
        msg = (
            "invalid request: invalid message provided at index 3: "
            "`tool plan` cannot be used with this model."
        )
        assert pf.is_provider_incompatible_error(Exception(msg)) is True

    def test_tool_plan_marker_case_insensitive(self):
        assert (
            pf.is_provider_incompatible_error(Exception("TOOL_PLAN rejected")) is True
        )

    def test_quota_not_incompatible(self):
        assert (
            pf.is_provider_incompatible_error(Exception("429 quota exceeded")) is False
        )

    def test_transient_not_incompatible(self):
        assert (
            pf.is_provider_incompatible_error(Exception("connection refused")) is False
        )

    def test_unrelated_value_error(self):
        assert pf.is_provider_incompatible_error(ValueError("bad input")) is False

    def test_empty(self):
        assert pf.is_provider_incompatible_error(Exception("")) is False


# ---------------------------------------------------------------------------
# _provider_has_key
# ---------------------------------------------------------------------------


class TestProviderHasKey:
    def test_ollama_always_true(self):
        assert pf._provider_has_key("ollama") is True

    def test_openrouter_true_when_key_configured(self):
        from backend.settings import settings

        with patch.object(settings, "openrouter_api_key", "sk-or-v1-abc"):
            assert pf._provider_has_key("openrouter") is True

    def test_openrouter_false_when_key_absent(self):
        from backend.settings import settings

        with patch.object(settings, "openrouter_api_key", None):
            assert pf._provider_has_key("openrouter") is False

    def test_unknown_provider_false(self):
        assert pf._provider_has_key("does-not-exist") is False

    def test_google_genai_true_when_key_configured(self):
        """Regressão: o keymap usava 'google-genai' (hífen) mas o provider
        chega normalizado com underscore (mesma convenção de _provider_of) —
        o Gemini nunca era reconhecido como tendo key, então nunca entrava
        na cadeia de fallback de outro provider."""
        from backend.settings import settings

        with patch.object(settings, "google_api_key", "AIza-fake"):
            assert pf._provider_has_key("google_genai") is True

    def test_google_genai_false_when_key_absent(self):
        from backend.settings import settings

        with patch.object(settings, "google_api_key", None):
            assert pf._provider_has_key("google_genai") is False

    def test_llamacpp_is_available_with_external_url(self):
        from backend.settings import settings

        with patch.object(settings, "llamacpp_base_url", "http://127.0.0.1:18082/v1"):
            assert pf._provider_has_key("llamacpp") is True

    def test_llamacpp_is_available_for_managed_sidecar(self):
        from backend.settings import settings

        with (
            patch.object(settings, "llamacpp_base_url", None),
            patch(
                "backend.services.llamacpp_sidecar.llamacpp_status",
                return_value={"running": True},
            ),
        ):
            assert pf._provider_has_key("llamacpp") is True


# ---------------------------------------------------------------------------
# get_fallback_chain
# ---------------------------------------------------------------------------


def _patch_env(order, keyed_providers):
    """Patcha runtime_settings.get(llm_fallback_order) e _provider_has_key."""
    rt = patch.object(pf, "_fallback_order", return_value=list(order))
    keys = patch.object(
        pf,
        "_provider_has_key",
        side_effect=lambda p: p in keyed_providers,
    )
    return rt, keys


class TestGetFallbackChain:
    def test_removes_current_provider(self):
        order = ["openai:gpt-4o", "google-genai:gemini-2.5-flash", "cohere:command-a"]
        rt, keys = _patch_env(order, {"openai", "google_genai", "cohere"})
        with rt, keys:
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == ["google-genai:gemini-2.5-flash", "cohere:command-a"]

    def test_removes_providers_without_key(self):
        order = ["openai:gpt-4o", "google-genai:gemini-2.5-flash", "cohere:command-a"]
        rt, keys = _patch_env(order, {"openai", "google_genai"})  # sem cohere
        with rt, keys:
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == ["google-genai:gemini-2.5-flash"]

    def test_empty_order(self):
        rt, keys = _patch_env([], {"openai", "google_genai"})
        with rt, keys:
            assert pf.get_fallback_chain("openai:gpt-4o") == []

    def test_current_provider_appears_multiple_times_all_removed(self):
        order = ["openai:gpt-4o", "openai:gpt-4o-mini", "cohere:command-a"]
        rt, keys = _patch_env(order, {"openai", "cohere"})
        with rt, keys:
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == ["cohere:command-a"]

    def test_preserves_order(self):
        order = ["cohere:command-a", "google-genai:gemini-2.5-flash"]
        rt, keys = _patch_env(order, {"cohere", "google_genai"})
        with rt, keys:
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == ["cohere:command-a", "google-genai:gemini-2.5-flash"]

    def test_no_keyed_providers(self):
        order = ["cohere:command-a", "google-genai:gemini-2.5-flash"]
        rt, keys = _patch_env(order, set())
        with rt, keys:
            assert pf.get_fallback_chain("openai:gpt-4o") == []


# ---------------------------------------------------------------------------
# get_fallback_chain — fallback somente por configuração explícita
# ---------------------------------------------------------------------------


class TestGetFallbackChainAutoDefault:
    def test_fallback_nao_configurado_devolve_cadeia_vazia(self):
        """Fallback não configurado não troca de provider automaticamente."""
        from backend.settings import settings

        rt = patch.object(pf, "_fallback_order", return_value=[])
        with (
            rt,
            patch.object(settings, "google_api_key", "AIza-fake"),
            patch.object(settings, "openai_api_key", None),
            patch.object(settings, "anthropic_api_key", None),
            patch.object(settings, "cohere_api_key", "co-fake"),
            patch.object(settings, "openrouter_api_key", None),
        ):
            chain = pf.get_fallback_chain("google_genai:gemini-2.5-pro")
        assert chain == []

    def test_normalizes_catalog_provider_aliases_for_credentials(
        self: TestGetFallbackChainAutoDefault,
    ) -> None:
        order = [
            "google-genai:gemini-2.5-flash",
            "nine-router:cx/gpt-5.6-luna",
        ]
        rt, keys = _patch_env(order, {"google_genai", "nine_router"})
        with rt, keys:
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == order

    def test_fallback_nao_configurado_ignora_ollama(self):
        from backend.settings import settings

        rt = patch.object(pf, "_fallback_order", return_value=[])
        with (
            rt,
            patch.object(settings, "google_api_key", None),
            patch.object(settings, "openai_api_key", None),
            patch.object(settings, "anthropic_api_key", None),
            patch.object(settings, "cohere_api_key", None),
            patch.object(settings, "openrouter_api_key", None),
        ):
            chain = pf.get_fallback_chain("openai:gpt-4o")
        assert chain == []
        # o primário nunca aparece na própria cadeia auto-gerada.
        assert all(not c.startswith("openai:") for c in chain)

    def test_fallback_explicitamente_vazio_devolve_cadeia_vazia(self):
        from backend.settings import settings

        rt = patch.object(pf, "_fallback_order", return_value=[])
        with (
            rt,
            patch.object(settings, "google_api_key", None),
            patch.object(settings, "openai_api_key", None),
            patch.object(settings, "anthropic_api_key", None),
            patch.object(settings, "cohere_api_key", None),
            patch.object(settings, "openrouter_api_key", None),
        ):
            chain = pf.get_fallback_chain("ollama:gpt-oss:20b")
        assert chain == []

    def test_modelo_openrouter_nao_cria_fallback_google(self):
        rt = patch.object(pf, "_fallback_order", return_value=[])
        with rt:
            assert (
                pf.get_fallback_chain("openrouter:deepseek/deepseek-v4-flash-0731")
                == []
            )

    def test_configuracao_explicita_com_uma_entrada_nao_e_substituida(self):
        """Erro/borda: configuração manual do usuário, mesmo incompleta (1
        entrada só), tem precedência sobre o default automático — não
        adiciona providers extras que o usuário não escolheu."""
        order = ["cohere:command-a-custom"]
        rt, keys = _patch_env(order, {"cohere"})
        with rt, keys:
            chain = pf.get_fallback_chain("google_genai:gemini-2.5-pro")
        assert chain == ["cohere:command-a-custom"]


# ---------------------------------------------------------------------------
# record_switch / drain_switches
# ---------------------------------------------------------------------------


class TestSwitchQueue:
    def test_drain_empty(self):
        pf.drain_switches()  # limpa estado anterior
        assert pf.drain_switches() == []

    def test_record_then_drain(self):
        pf.drain_switches()
        pf.record_switch("openai:gpt-4o", "google-genai:gemini-2.5-flash")
        drained = pf.drain_switches()
        assert drained == [
            {"from": "openai:gpt-4o", "to": "google-genai:gemini-2.5-flash"}
        ]

    def test_drain_clears(self):
        pf.drain_switches()
        pf.record_switch("a:1", "b:2")
        pf.drain_switches()
        assert pf.drain_switches() == []

    def test_multiple_switches_in_order(self):
        pf.drain_switches()
        pf.record_switch("a:1", "b:2")
        pf.record_switch("b:2", "c:3")
        drained = pf.drain_switches()
        assert [d["from"] for d in drained] == ["a:1", "b:2"]
        assert [d["to"] for d in drained] == ["b:2", "c:3"]


# ---------------------------------------------------------------------------
# try_with_fallback
# ---------------------------------------------------------------------------


class TestTryWithFallback:
    async def test_success_first_no_switch(self):
        pf.drain_switches()
        calls = []

        async def fn(mid):
            calls.append(mid)
            return f"ok:{mid}"

        out = await pf.try_with_fallback(fn, "openai:gpt-4o")
        assert out == "ok:openai:gpt-4o"
        assert calls == ["openai:gpt-4o"]
        assert pf.drain_switches() == []

    async def test_switch_once(self):
        pf.drain_switches()
        switches = []

        async def fn(mid):
            if mid == "openai:gpt-4o":
                raise Exception("429 RESOURCE_EXHAUSTED")
            return f"ok:{mid}"

        with patch.object(
            pf, "get_fallback_chain", return_value=["google-genai:gemini-2.5-flash"]
        ):
            out = await pf.try_with_fallback(
                fn, "openai:gpt-4o", on_switch=lambda a, b: switches.append((a, b))
            )
        assert out == "ok:google-genai:gemini-2.5-flash"
        assert switches == [("openai:gpt-4o", "google-genai:gemini-2.5-flash")]
        assert pf.drain_switches() == [
            {"from": "openai:gpt-4o", "to": "google-genai:gemini-2.5-flash"}
        ]

    async def test_switch_twice(self):
        async def fn(mid):
            if mid != "cohere:command-a":
                raise Exception("quota exceeded")
            return "ok"

        with patch.object(
            pf,
            "get_fallback_chain",
            return_value=["google-genai:gemini-2.5-flash", "cohere:command-a"],
        ):
            out = await pf.try_with_fallback(fn, "openai:gpt-4o")
        assert out == "ok"

    async def test_exhausts_raises_quota(self):
        async def fn(mid):
            raise Exception("429 quota")

        with patch.object(
            pf, "get_fallback_chain", return_value=["google-genai:gemini-2.5-flash"]
        ):
            with pytest.raises(pf.QuotaExhaustedError):
                await pf.try_with_fallback(fn, "openai:gpt-4o")

    async def test_empty_chain_raises_quota(self):
        async def fn(mid):
            raise Exception("rate limit")

        with patch.object(pf, "get_fallback_chain", return_value=[]):
            with pytest.raises(pf.QuotaExhaustedError):
                await pf.try_with_fallback(fn, "openai:gpt-4o")

    async def test_non_quota_propagates_immediately(self):
        calls = []

        async def fn(mid):
            calls.append(mid)
            raise ValueError("boom")

        with patch.object(
            pf, "get_fallback_chain", return_value=["google-genai:gemini-2.5-flash"]
        ):
            with pytest.raises(ValueError):
                await pf.try_with_fallback(fn, "openai:gpt-4o")
        assert calls == ["openai:gpt-4o"]  # não tentou fallback
