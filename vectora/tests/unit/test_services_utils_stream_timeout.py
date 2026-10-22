"""``backend/llm/fallback_chat_client::load_chat_client`` — nenhum provider pode
impor timeout de silêncio entre chunks. Modelos de raciocínio (reasoning)
ficam minutos "pensando" sem emitir nenhum token — isso é comportamento
normal, não falha de conexão.

"openai", "openrouter" e "nine_router" usam clients nativos, que controlam
o timeout HTTP diretamente — sem nenhum timeout de silêncio entre chunks
imposto por biblioteca externa. Não há mais nenhum caminho de código capaz
de instanciar um chat model de terceiros, então o guard de regressão vira
o próprio isinstance positivo na classe nativa.
"""

from __future__ import annotations

import pytest

from backend.llm.fallback_chat_client import load_chat_client
from backend.services.env import GetEnvError


def test_openai_usa_cliente_nativo_e_nao_chat_openai(monkeypatch):
    from backend.llm.openai.chat_client import OpenAIChatClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    modelo = load_chat_client("openai:gpt-5")

    assert isinstance(modelo, OpenAIChatClient)


def test_openrouter_usa_cliente_nativo_e_nao_chat_openai(monkeypatch):
    from backend.llm.openrouter.chat_client import OpenRouterChatClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    modelo = load_chat_client("openrouter:openrouter/auto")

    assert isinstance(modelo, OpenRouterChatClient)


def test_openrouter_sem_api_key_levanta_erro_claro(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(GetEnvError, match="OPENROUTER_API_KEY"):
        load_chat_client("openrouter:openrouter/auto")


def test_nine_router_reusa_cliente_nativo_do_openrouter_com_base_url_trocada(
    monkeypatch,
):
    """9Router fala o mesmo protocolo Chat Completions que o adapter nativo
    do OpenRouter já implementa — reusa ``OpenRouterChatClient``/
    ``OpenRouterClient`` com ``base_url`` apontando pro 9Router, em vez da
    Responses API que ``OpenAIChatClient`` consome (9Router não a expõe)."""
    from backend.llm.openrouter.chat_client import OpenRouterChatClient
    from backend.settings import settings

    monkeypatch.setattr(settings, "nine_router_base_url", "http://localhost:20128/v1")
    monkeypatch.setattr(settings, "nine_router_api_key", "9r-test-key")

    modelo = load_chat_client("nine_router:cc/claude-opus-4-7")

    assert isinstance(modelo, OpenRouterChatClient)
    assert modelo.client._base_url == "http://localhost:20128/v1"
    assert modelo.model == "cc/claude-opus-4-7"


def test_nine_router_sem_config_levanta_erro_claro(monkeypatch):
    from backend.settings import settings

    monkeypatch.setattr(settings, "nine_router_base_url", None)
    monkeypatch.setattr(settings, "nine_router_api_key", None)

    with pytest.raises(ValueError, match="9Router"):
        load_chat_client("nine_router:cc/claude-opus-4-7")


def test_ollama_usa_cliente_nativo_e_nao_init_chat_model(monkeypatch):
    from backend.llm.ollama.chat_client import OllamaChatClient

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    modelo = load_chat_client("ollama:gpt-oss:20b")

    assert isinstance(modelo, OllamaChatClient)
    assert modelo.client.base_url == "http://127.0.0.1:11434"


def test_llamacpp_usa_projecao_compacta_para_modelos_locais(monkeypatch):
    """O provider local deve preservar as regras essenciais sem enviar ao
    modelo pequeno todo o catálogo de ferramentas e o contexto operacional."""
    from backend.llm.openrouter.chat_client import OpenRouterChatClient
    from backend.settings import settings

    monkeypatch.setenv("LLAMACPP_MODE", "external")
    monkeypatch.setattr(settings, "llamacpp_base_url", "http://127.0.0.1:18080/v1")
    monkeypatch.setattr(settings, "llamacpp_api_key", None)

    modelo = load_chat_client("llamacpp:qwen2.5-0.5b-instruct-q2-k")

    assert isinstance(modelo, OpenRouterChatClient)
    assert modelo.compact_system_prompt is True
    assert modelo.client._base_url == "http://127.0.0.1:18080/v1"
