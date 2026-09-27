"""Library — tools de consulta ao catálogo + HITL nas tools sensíveis.

Sem as tools de consulta o agente só consegue instalar o que o próprio usuário
já nomeou: não dá pra sugerir com informação real. E a lista de HITL precisa
separar leitura de escrita — pedir aprovação pra listar catálogo é fricção sem
ganho, não pedir pra desinstalar/publicar é risco real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.tools import library


@pytest.mark.asyncio
async def test_list_mcp_catalog_filtra_e_degrada_sem_lancar(monkeypatch):
    async def _registry():
        return [
            SimpleNamespace(
                id="github", name="GitHub", description="Repos e PRs", env_vars=["T"]
            ),
            SimpleNamespace(
                id="postgres", name="Postgres", description="SQL", env_vars=[]
            ),
        ]

    monkeypatch.setattr("backend.api.handlers.mcp_marketplace.list_registry", _registry)

    todos = json.loads(await library.list_mcp_catalog())
    assert todos["total"] == 2
    assert {i["id"] for i in todos["items"]} == {"github", "postgres"}

    filtrado = json.loads(await library.list_mcp_catalog(query="repos"))
    assert [i["id"] for i in filtrado["items"]] == ["github"]

    # Erro/borda: filtro sem resultado é lista vazia, não erro — o LLM precisa
    # poder dizer "não achei nada" em vez de receber uma exceção.
    vazio = json.loads(await library.list_mcp_catalog(query="zzz"))
    assert vazio == {"items": [], "total": 0}

    # Erro/borda: registry fora do ar degrada com lista vazia + erro descrito,
    # nunca propaga.
    async def _explode():
        raise OSError("registry inacessível")

    monkeypatch.setattr("backend.api.handlers.mcp_marketplace.list_registry", _explode)
    caido = json.loads(await library.list_mcp_catalog())
    assert caido["items"] == []
    assert "registry inacessível" in caido["error"]


@pytest.mark.asyncio
async def test_list_skills_catalog_happy_e_catalogo_vazio(monkeypatch):
    async def _catalog(_kind):
        return [{"id": "revisar-pr", "name": "Revisar PR", "description": "Como"}]

    monkeypatch.setattr("backend.services.registry_client.fetch_catalog", _catalog)
    out = json.loads(await library.list_skills_catalog())
    assert [i["id"] for i in out["items"]] == ["revisar-pr"]

    async def _vazio(_kind):
        return []

    monkeypatch.setattr("backend.services.registry_client.fetch_catalog", _vazio)
    assert json.loads(await library.list_skills_catalog())["total"] == 0


@pytest.mark.asyncio
async def test_list_memory_bucket_catalog_happy_e_falha(monkeypatch):
    async def _catalog():
        return [{"id": "leis-br", "name": "Leis BR", "description": "Legislação"}]

    monkeypatch.setattr("backend.services.memory_buckets.list_catalog", _catalog)
    out = json.loads(await library.list_memory_bucket_catalog())
    assert [i["id"] for i in out["items"]] == ["leis-br"]

    async def _explode():
        raise RuntimeError("library fora do ar")

    monkeypatch.setattr("backend.services.memory_buckets.list_catalog", _explode)
    caido = json.loads(await library.list_memory_bucket_catalog())
    assert caido["items"] == []
    assert "library fora do ar" in caido["error"]


@pytest.mark.asyncio
async def test_catalogo_grande_e_truncado_com_total_real(monkeypatch):
    # Regressão de contexto: o registry oficial tem centenas de entradas e
    # despejar tudo gastaria a janela do LLM. `total` continua o número real
    # pra o agente saber que há mais.
    async def _registry():
        return [
            SimpleNamespace(id=f"c{i}", name=f"C{i}", description="x", env_vars=[])
            for i in range(200)
        ]

    monkeypatch.setattr("backend.api.handlers.mcp_marketplace.list_registry", _registry)
    out = json.loads(await library.list_mcp_catalog())
    assert len(out["items"]) == library._CATALOG_PAGE_SIZE
    assert out["total"] == 200


def test_hitl_cobre_escrita_e_deixa_leitura_livre():
    from backend.engine.hitl import REQUIRE_APPROVAL

    # Toda tool que remove, publica ou grava credencial precisa pausar.
    for sensivel in (
        "install_mcp_from_registry",
        "install_skill_from_catalog",
        "install_memory_bucket",
        "uninstall_mcp",
        "delete_skill",
        "publish_memory_bucket_tool",
        "save_mcp_env_var",
    ):
        assert sensivel in REQUIRE_APPROVAL, f"{sensivel} deveria exigir aprovação"

    # Erro/borda: leitura pura NÃO pode entrar — HITL aqui seria fricção sem
    # ganho nenhum de segurança, e o agente perderia a capacidade de consultar
    # o catálogo antes de sugerir.
    for leitura in (
        "verify_skill",
        "list_mcp_catalog",
        "list_skills_catalog",
        "list_memory_bucket_catalog",
    ):
        assert leitura not in REQUIRE_APPROVAL, f"{leitura} não deveria exigir HITL"
