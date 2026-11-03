"""``load_native_llm()`` (``backend/services/utils.py``) — resolução de
provider/modelo sem chamada de rede real (``load_chat_client`` mockado).

Cobre a regressão corrigida: `RuntimeSettings` costumava inventar
"google-genai/gemini-2.5-flash" como provider/modelo ativo em qualquer
instalação sem setup completo — qualquer chamador de `load_native_llm()`
sem `model_id` explícito (remember automático, consolidação de memória,
curator, chat principal sem seleção) herdava esse valor nunca escolhido
pelo usuário. Agora `load_native_llm()` nunca inventa provider/modelo —
levanta erro claro quando nada foi configurado.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.llm.fallback_chat_client import load_chat_client
from backend.services.utils import load_native_llm
from backend.settings import settings
from backend.workspace.runtime_settings import runtime_settings


@pytest.fixture(autouse=True)
def _isolated_runtime_settings(monkeypatch):
    """Nunca deixa este teste ler/escrever o `checkpoints.db` real do
    usuário — cada teste começa com provider/modelo genuinamente vazios."""
    monkeypatch.setattr(runtime_settings, "_data", {})
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GOOGLE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def _mock_load_chat_client():
    return patch(
        "backend.llm.fallback_chat_client.load_chat_client",
        return_value=MagicMock(),
    )


def test_sem_provider_nem_modelo_configurado_levanta_erro_claro():
    """Nada foi configurado ainda (fresh install) — nunca inventa
    google-genai/gemini como default silencioso, levanta erro."""
    with _mock_load_chat_client():
        with pytest.raises(ValueError, match=r"[Nn]enhum provider"):
            load_native_llm()


def test_provider_configurado_sem_modelo_levanta_erro_claro():
    """Erro/borda: usuário escolheu um provider mas nunca um modelo
    (ex.: wizard interrompido) — não cai num modelo de outro provider
    nem num hardcoded de fábrica, erro específico do provider ativo."""
    runtime_settings.set("active_provider", "google-genai")
    with _mock_load_chat_client():
        with pytest.raises(ValueError, match="google_genai"):
            load_native_llm()


def test_provider_e_modelo_configurados_resolve_normalmente():
    """Caminho feliz: com provider+modelo explicitamente configurados via
    `set_active_model`, resolve pro model_id certo — sem qualquer
    dependência de default hardcoded."""
    runtime_settings.set_active_model("openai", "gpt-5.2")
    with _mock_load_chat_client() as mock_load:
        load_native_llm()
    mock_load.assert_called_once_with("openai:gpt-5.2")


def test_env_var_sobrepoe_active_model(monkeypatch):
    """`GOOGLE_MODEL` no ambiente continua tendo prioridade sobre
    `runtime_settings.active_model` — comportamento preexistente mantido."""
    runtime_settings.set_active_model("google-genai", "gemini-3.6-flash")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-3.6-pro")
    with _mock_load_chat_client() as mock_load:
        load_native_llm()
    mock_load.assert_called_once_with("google_genai:gemini-3.6-pro")


def test_model_id_explicito_nunca_depende_de_runtime_settings():
    """Passar `model_id` explícito (troca de modelo por request) resolve
    sozinho, sem tocar `runtime_settings` — nada configurado ali e ainda
    assim funciona."""
    with _mock_load_chat_client() as mock_load:
        load_native_llm("anthropic:claude-opus-5")
    mock_load.assert_called_once_with("anthropic:claude-opus-5")


@pytest.mark.parametrize(
    ("contract", "expected_type"),
    [
        ("openai", "OpenRouterChatClient"),
        ("ollama", "OllamaChatClient"),
    ],
)
def test_dmr_chat_client_respeita_contrato_anunciado(
    monkeypatch: pytest.MonkeyPatch,
    contract: str,
    expected_type: str,
) -> None:
    """O cliente DMR deve usar o contrato descoberto, não sempre OpenAI."""
    monkeypatch.setattr(settings, "dmr_base_url", "http://127.0.0.1:12434")
    monkeypatch.setattr(settings, "dmr_contract", contract)
    client = load_chat_client("dmr:hf.co/Qwen/Qwen3-0.6B")
    assert type(client).__name__ == expected_type
