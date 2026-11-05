"""Tools de Home Assistant — API REST da instância do próprio usuário.

O que diferencia estas tools das outras: `ha_call_service` age no **mundo
físico** (destrancar porta, desligar alarme). Não é um arquivo que dá pra
restaurar do git. Por isso ela é a única das quatro em `_REQUIRE_APPROVAL`,
e os testes travam isso nos dois sentidos — a de escrita dentro, as três de
leitura fora (HITL em leitura é fricção sem ganho).

Cada caminho feliz tem o par de erro/borda no mesmo teste.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from backend.tools import homeassistant as ha

_RealAsyncClient = httpx.AsyncClient


@contextmanager
def _mocked_client(handler):
    """Substitui `httpx.AsyncClient` real por um `MockTransport` — a tool não
    recebe client por parâmetro (deixaria de fazer parte do schema exposto
    ao LLM), então o teste intercepta a própria construção do client dentro
    de `ha_request`. Uma instância nova por chamada (não uma reusada): o
    `async with` de `ha_request` fecha o client no fim de cada chamada —
    reusar a mesma instância quebraria na segunda chamada do mesmo teste.
    Usa `_RealAsyncClient` (capturada antes do patch) — chamar
    `httpx.AsyncClient(...)` aqui dentro recairia no próprio mock."""

    def _new_client(*_args, **_kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    with patch("httpx.AsyncClient", side_effect=_new_client):
        yield


@pytest.fixture(autouse=True)
def _credenciais(monkeypatch):
    from backend.settings import settings

    monkeypatch.setattr(settings, "home_assistant_url", "http://ha.local:8123")
    monkeypatch.setattr(settings, "home_assistant_token", "tok")


_ESTADOS = [
    {"entity_id": "light.sala", "state": "on", "attributes": {"brightness": 200}},
    {"entity_id": "sensor.temperatura", "state": "21.5", "attributes": {}},
]


class TestCredenciais:
    @pytest.mark.asyncio
    async def test_sem_credencial_avisa_e_nao_faz_chamada(self, monkeypatch):
        """Erro/borda: sem URL/token o certo é dizer o que configurar. Tentar
        a chamada daria `ConnectError` cru, que não ensina nada."""
        from backend.settings import settings

        monkeypatch.setattr(settings, "home_assistant_token", "")

        def _nunca(_req):
            raise AssertionError("não deveria chamar o Home Assistant")

        with _mocked_client(_nunca):
            saida = json.loads(await ha.ha_list_entities(domain=""))

        assert "error" in saida
        assert "HOME_ASSISTANT_TOKEN" in saida["error"]

    @pytest.mark.asyncio
    async def test_token_rejeitado_diz_que_e_o_token(self):
        """Erro/borda: 401 do HA é token inválido/expirado — a mensagem
        precisa distinguir isso de instância fora do ar."""

        def _handler(_req):
            return httpx.Response(401, json={"message": "Unauthorized"})

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_list_entities(domain=""))

        assert "error" in saida
        assert "token" in saida["error"].lower()


class TestListEntities:
    @pytest.mark.asyncio
    async def test_lista_e_filtra_por_dominio(self):
        def _handler(req):
            assert req.url.path == "/api/states"
            assert req.headers["Authorization"] == "Bearer tok"
            return httpx.Response(200, json=_ESTADOS)

        with _mocked_client(_handler):
            todas = json.loads(await ha.ha_list_entities(domain=""))
            assert len(todas["entities"]) == 2

            so_luz = json.loads(await ha.ha_list_entities(domain="light"))
            assert [e["entity_id"] for e in so_luz["entities"]] == ["light.sala"]

    @pytest.mark.asyncio
    async def test_dominio_sem_nenhuma_entidade_devolve_lista_vazia(self):
        """Erro/borda: lista vazia não é erro — é "não tem lâmpada". Mas o
        aviso precisa vir junto, senão o LLM conclui que a casa não tem luz
        quando na verdade o filtro estava errado."""

        def _handler(_req):
            return httpx.Response(200, json=_ESTADOS)

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_list_entities(domain="climate"))

        assert saida["entities"] == []
        assert "climate" in saida["warning"]


class TestGetState:
    @pytest.mark.asyncio
    async def test_estado_de_entidade_existente(self):
        def _handler(req):
            assert req.url.path == "/api/states/light.sala"
            return httpx.Response(200, json=_ESTADOS[0])

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_get_state(entity_id="light.sala"))

        assert saida["state"] == "on"

    @pytest.mark.asyncio
    async def test_entidade_inexistente_nomeia_a_entidade(self):
        """Erro/borda: 404 genérico faria o LLM achar que o HA caiu."""

        def _handler(_req):
            return httpx.Response(404, json={"message": "Entity not found"})

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_get_state(entity_id="light.inexistente"))

        assert "light.inexistente" in saida["error"]


class TestListServices:
    @pytest.mark.asyncio
    async def test_servicos_do_dominio_pedido(self):
        def _handler(_req):
            return httpx.Response(
                200,
                json=[
                    {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
                    {"domain": "lock", "services": {"unlock": {}}},
                ],
            )

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_list_services(domain="light"))

        assert sorted(saida["services"]) == ["turn_off", "turn_on"]

    @pytest.mark.asyncio
    async def test_corpo_fora_do_formato_vira_erro_tipado(self):
        """Erro/borda: HA atrás de proxy devolvendo HTML. Sem tratamento o
        `.json()` estoura com JSONDecodeError cru dentro do grafo."""

        def _handler(_req):
            return httpx.Response(200, text="<html>login</html>")

        with _mocked_client(_handler):
            saida = json.loads(await ha.ha_list_services(domain="light"))

        assert "error" in saida


class TestCallService:
    @pytest.mark.asyncio
    async def test_chama_servico_e_devolve_o_que_mudou(self):
        def _handler(req):
            assert req.url.path == "/api/services/light/turn_on"
            assert json.loads(req.content) == {
                "entity_id": "light.sala",
                "brightness": 120,
            }
            return httpx.Response(200, json=[_ESTADOS[0]])

        with _mocked_client(_handler):
            saida = json.loads(
                await ha.ha_call_service(
                    domain="light",
                    service="turn_on",
                    entity_id="light.sala",
                    data={"brightness": 120},
                )
            )

        assert saida["changed"][0]["entity_id"] == "light.sala"

    @pytest.mark.asyncio
    async def test_servico_inexistente_nao_finge_sucesso(self):
        """Erro/borda: o HA responde 400 pra serviço desconhecido. Devolver
        `ok` faria o agente afirmar que apagou a luz sem ter apagado."""

        def _handler(_req):
            return httpx.Response(400, json={"message": "Service not found"})

        with _mocked_client(_handler):
            saida = json.loads(
                await ha.ha_call_service(
                    domain="light",
                    service="explodir",
                    entity_id="light.sala",
                    data={},
                )
            )

        assert "error" in saida
        assert "changed" not in saida


class TestAprovacaoHumana:
    def test_so_a_tool_de_acao_exige_aprovacao(self):
        """Invariante de produto: agir na casa pausa; ler não.

        `ha_call_service` destranca porta e desliga alarme — não há "desfazer"
        no mundo físico, ao contrário de um arquivo que volta pelo git.
        """
        from backend.engine.hitl import REQUIRE_APPROVAL

        assert "ha_call_service" in REQUIRE_APPROVAL
        for leitura in ("ha_list_entities", "ha_get_state", "ha_list_services"):
            assert leitura not in REQUIRE_APPROVAL

    def test_as_quatro_tools_chegam_no_agente(self):
        from backend.nodes.tools import ALL_TOOLS

        nomes = {t.name for t in ALL_TOOLS}
        assert {
            "ha_list_entities",
            "ha_get_state",
            "ha_list_services",
            "ha_call_service",
        } <= nomes

    def test_schema_das_quatro_tools_nunca_expoe_parametro_sem_tipo(self):
        """Regressão: `http_client` (parâmetro de injeção de teste) vazou
        pro schema OpenAI-function exposto ao LLM/providers — um parâmetro
        `Any` sem `type` resolvido quebra o Cohere v2, que exige `type`
        sempre presente (string ou array de strings)."""
        from backend.nodes.tools import ALL_TOOLS

        alvo = {
            "ha_list_entities",
            "ha_get_state",
            "ha_list_services",
            "ha_call_service",
        }
        tools = [t for t in ALL_TOOLS if t.name in alvo]
        assert len(tools) == 4

        for tool in tools:
            schema = tool.openai_schema()["function"]
            properties = schema.get("parameters", {}).get("properties", {})
            assert "http_client" not in properties
            for pname, pdef in properties.items():
                has_type = "type" in pdef and pdef["type"] is not None
                has_any_of = "anyOf" in pdef
                assert has_type or has_any_of, (
                    f"{tool.name}.{pname} sem type/anyOf resolvido: {pdef}"
                )


class TestCatalogoDeIntegracao:
    def test_home_assistant_aparece_com_os_dois_campos(self):
        """Sem `extra_vars` a UI pediria só o token e a URL ficaria sem onde
        ser preenchida — a integração nasceria inutilizável."""
        from backend.api.handlers.oauth import INTEGRATIONS_REGISTRY

        entrada = next(i for i in INTEGRATIONS_REGISTRY if i["id"] == "home-assistant")

        assert entrada["kind"] == "apikey"
        assert entrada["env_var"] == "HOME_ASSISTANT_TOKEN"
        assert "HOME_ASSISTANT_URL" in entrada["extra_vars"]

    def test_credencial_salva_pela_ui_chega_no_processo(self):
        """Erro/borda do bug do Connect: gravar só no banco por-usuário deixa
        `os.environ` sem a credencial e a tool segue "não configurada"."""
        from backend.services.env_keys import RUNTIME_ENV_KEYS

        assert "HOME_ASSISTANT_TOKEN" in RUNTIME_ENV_KEYS
        assert "HOME_ASSISTANT_URL" in RUNTIME_ENV_KEYS
