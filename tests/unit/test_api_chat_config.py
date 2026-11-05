"""Tests para a montagem do RunnableConfig em src/api/handlers/chat.py.

Cobre R2 (permission_mode) e R4 (reasoning_effort): o ChatConfig do request
deve se traduzir corretamente para o dict ``configurable`` consumido pelo grafo.
"""

from __future__ import annotations

import pytest

from backend.api.handlers.chat import (
    _build_configurable,
    _resolve_image_fallback_model,
    _resolve_workspace_id,
)
from backend.api.schemas import ChatConfig

# ---------------------------------------------------------------------------
# Campos sempre presentes
# ---------------------------------------------------------------------------


def test_thread_and_user_always_present():
    cfg = _build_configurable(ChatConfig(), "thread-1", "user-1")
    assert cfg["thread_id"] == "thread-1"
    assert cfg["user_id"] == "user-1"


def test_optional_fields_absent_by_default():
    """Sem valores, os campos opcionais não entram no configurable."""
    cfg = _build_configurable(ChatConfig(), "t", "u")
    assert "workspace_id" not in cfg
    assert "custom_system_prompt" not in cfg
    assert "reasoning_effort" not in cfg


# ---------------------------------------------------------------------------
# R2 — permission_mode
# ---------------------------------------------------------------------------


def test_permission_mode_default_is_empty_sentinel():
    """O modo ausente fica vazio para permitir reidratar a sessão persistida."""
    cfg = _build_configurable(ChatConfig(), "t", "u")
    assert "permission_mode" not in cfg


def test_permission_mode_passthrough():
    for mode in ("ask", "accept_edits", "plan", "auto", "bypass"):
        cfg = _build_configurable(ChatConfig(permission_mode=mode), "t", "u")
        assert cfg["permission_mode"] == mode


def test_permission_mode_empty_string_omitted():
    cfg = _build_configurable(ChatConfig(permission_mode=""), "t", "u")
    assert "permission_mode" not in cfg


def test_permission_mode_rejeita_valor_fora_do_contrato() -> None:
    """A API não deve aceitar modos que o motor não sabe interpretar."""
    with pytest.raises(ValueError):
        ChatConfig.model_validate({"permission_mode": "modo-invalido"})


# ---------------------------------------------------------------------------
# R4 — reasoning_effort
# ---------------------------------------------------------------------------


def test_reasoning_effort_passthrough():
    cfg = _build_configurable(ChatConfig(reasoning_effort="high"), "t", "u")
    assert cfg["reasoning_effort"] == "high"


def test_reasoning_effort_default_omitted():
    """Default vazio → modelo usa seu próprio default (campo ausente)."""
    cfg = _build_configurable(ChatConfig(), "t", "u")
    assert "reasoning_effort" not in cfg


# ---------------------------------------------------------------------------
# Outros campos opcionais
# ---------------------------------------------------------------------------


def test_workspace_and_prompt_passthrough():
    cfg = _build_configurable(
        ChatConfig(workspace_id="ws1", custom_system_prompt="seja conciso"),
        "t",
        "u",
    )
    assert cfg["workspace_id"] == "ws1"
    assert cfg["custom_system_prompt"] == "seja conciso"


# ---------------------------------------------------------------------------
# Troca de modelo por request — config.model entra no configurable, com o
# provider normalizado para o formato canônico do init_chat_model (underscore).
# ---------------------------------------------------------------------------


def test_model_absent_by_default():
    cfg = _build_configurable(ChatConfig(), "t", "u")
    assert "model" not in cfg


def test_model_provider_hyphen_normalized_to_underscore():
    cfg = _build_configurable(
        ChatConfig(model="google-genai:gemini-2.5-flash"), "t", "u"
    )
    assert cfg["model"] == "google_genai:gemini-2.5-flash"


def test_model_provider_without_hyphen_passthrough():
    cfg = _build_configurable(ChatConfig(model="cohere:command-a-03-2025"), "t", "u")
    assert cfg["model"] == "cohere:command-a-03-2025"


def test_openrouter_model_preserves_slash_and_provider():
    cfg = _build_configurable(
        ChatConfig(model="openrouter:deepseek/deepseek-v4-flash-0731"), "t", "u"
    )
    assert cfg["model"] == "openrouter:deepseek/deepseek-v4-flash-0731"


def test_model_without_colon_passthrough():
    # Sem prefixo de provider: repassado como veio (não vira provider espúrio).
    cfg = _build_configurable(ChatConfig(model="gemini-2.5-flash"), "t", "u")
    assert cfg["model"] == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Item 3 — fork de checkpoint (editar mensagem / regenerar resposta)
# ---------------------------------------------------------------------------


def test_fork_from_checkpoint_id_present_included_absent_omitted():
    """checkpoint_id presente no configurable faz o histórico ramificar dali
    (edit/regenerate); ausente mantém o comportamento atual — resume do
    checkpoint mais recente da thread."""
    cfg = _build_configurable(ChatConfig(fork_from_checkpoint_id="cp-1234"), "t", "u")
    assert cfg["checkpoint_id"] == "cp-1234"

    cfg_default = _build_configurable(ChatConfig(), "t", "u")
    assert "checkpoint_id" not in cfg_default


# ---------------------------------------------------------------------------
# Workspace por sessão — _resolve_workspace_id
# ---------------------------------------------------------------------------


def test_resolve_keeps_explicit_workspace():
    """Workspace escolhido pelo cliente é mantido — sem criar pasta de sessão."""
    assert _resolve_workspace_id("ws-escolhido", "thread1", "u") == "ws-escolhido"


def test_resolve_reuses_active_workspace(monkeypatch):
    """Sem workspace pedido mas com um ativo, reusa o ativo (não cria por thread)."""
    calls = {}

    class _ActiveWs:
        id = "ws-ativo"

    class _FakeRegistry:
        def get_active(self, user_id=None):
            calls["get_active"] = user_id
            return _ActiveWs()

        def get_or_create_session_workspace(self, *_args, **_kwargs):
            calls["created"] = True  # não deve ser chamado
            raise AssertionError("não deveria criar workspace de sessão")

        def set_active(self, ws_id, user_id=None):
            calls["set_active"] = (ws_id, user_id)
            return True

    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry", _FakeRegistry()
    )
    result = _resolve_workspace_id("", "thread1", "u")
    assert result == "ws-ativo"
    assert calls["get_active"] == "u"
    assert "created" not in calls


def test_resolve_force_new_skips_active_workspace_reuse(monkeypatch):
    """force_new=True pula o reuso do workspace ativo e cria um dedicado —
    par de erro: force_new=False (default) continua reusando o ativo, mesmo
    registry fake, confirmando que o comportamento antigo não regrediu."""
    calls = {}

    class _ActiveWs:
        id = "ws-ativo"

    class _NewWs:
        id = "ws-novo-dedicado"

    class _FakeRegistry:
        def get_active(self, user_id=None):
            calls.setdefault("get_active_calls", 0)
            calls["get_active_calls"] += 1
            return _ActiveWs()

        def get_or_create_session_workspace(self, thread_id, user_id=None):
            calls["created_thread_id"] = thread_id
            return _NewWs()

        def set_active(self, ws_id, user_id=None):
            calls["set_active"] = (ws_id, user_id)
            return True

    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry", _FakeRegistry()
    )

    forced = _resolve_workspace_id("", "thread1", "u", force_new=True)
    assert forced == "ws-novo-dedicado"
    assert calls["created_thread_id"] == "thread1"
    assert calls["set_active"] == ("ws-novo-dedicado", "u")

    calls.clear()
    reused = _resolve_workspace_id("", "thread1", "u", force_new=False)
    assert reused == "ws-ativo"
    assert calls["get_active_calls"] == 1
    assert "created_thread_id" not in calls


def test_resolve_creates_session_workspace_when_no_active(monkeypatch):
    """Sem workspace pedido e sem ativo, deriva o padrão da sessão via registry."""
    calls = {}

    class _FakeWs:
        id = "sess-ws"

    class _FakeRegistry:
        def get_active(self, *_args, **_kwargs):
            return None

        def get_or_create_session_workspace(self, thread_id, user_id=None):
            calls["thread_id"] = thread_id
            calls["user_id"] = user_id
            return _FakeWs()

        def set_active(self, ws_id, user_id=None):
            calls["active"] = (ws_id, user_id)
            return True

    monkeypatch.setattr(
        "backend.workspace.workspace.workspace_registry", _FakeRegistry()
    )
    result = _resolve_workspace_id("", "thread1", "u")
    assert result == "sess-ws"
    assert calls["thread_id"] == "thread1"
    assert calls["active"] == ("sess-ws", "u")


# ---------------------------------------------------------------------------
# _resolve_image_fallback_model — modelo de fallback quando o
# ativo não processa imagem, em vez de sempre bloquear o envio.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sem_fallback_configurado_devolve_none(monkeypatch):
    from backend.workspace.runtime_settings import runtime_settings

    monkeypatch.setattr(runtime_settings, "get", lambda key, default=None: "")

    assert await _resolve_image_fallback_model() is None


@pytest.mark.asyncio
async def test_fallback_configurado_e_com_visao_e_devolvido(monkeypatch):
    from backend.workspace.runtime_settings import runtime_settings

    async def _sempre_com_visao(spec: str) -> bool:
        return True

    monkeypatch.setattr(
        runtime_settings,
        "get",
        lambda key, default=None: "google-genai:gemini-2.5-flash",
    )
    monkeypatch.setattr(
        "backend.api.handlers.chat._model_supports_vision", _sempre_com_visao
    )

    assert await _resolve_image_fallback_model() == "google-genai:gemini-2.5-flash"


@pytest.mark.asyncio
async def test_fallback_configurado_mas_tambem_sem_visao_devolve_none(monkeypatch):
    """Bad path: config inconsistente (fallback apontando pra outro modelo
    sem visão) não vira loop de bloqueio disfarçado de fallback — trata
    como se não houvesse fallback."""
    from backend.workspace.runtime_settings import runtime_settings

    async def _nunca_com_visao(spec: str) -> bool:
        return False

    monkeypatch.setattr(
        runtime_settings, "get", lambda key, default=None: "cohere:command-a"
    )
    monkeypatch.setattr(
        "backend.api.handlers.chat._model_supports_vision", _nunca_com_visao
    )

    assert await _resolve_image_fallback_model() is None
