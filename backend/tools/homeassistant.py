"""Home Assistant — API REST da instância do próprio usuário.

Self-hosted falando com self-hosted: a URL e o token de acesso de longa
duração são do Home Assistant de quem está usando o Vectora, nunca de um
serviço intermediário.

`ha_call_service` é a única tool do produto que age no **mundo físico** —
destrancar uma porta, desligar um alarme, abrir um portão. Não existe
`git checkout` pra isso, então ela pausa pra aprovação humana
(`_REQUIRE_APPROVAL`) enquanto as três de leitura passam direto.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_TIMEOUT_S = 20.0

_SEM_CREDENCIAL = (
    "Home Assistant não está configurado: preencha HOME_ASSISTANT_URL "
    "(ex.: http://homeassistant.local:8123) e HOME_ASSISTANT_TOKEN em "
    "Settings → Integrações. O token é um Long-Lived Access Token, criado "
    "no seu perfil do Home Assistant."
)


def _erro(mensagem: str) -> str:
    return json.dumps({"error": mensagem}, ensure_ascii=False)


def _credenciais() -> tuple[str, str]:
    from backend.settings import settings

    url = str(getattr(settings, "home_assistant_url", "") or "").strip().rstrip("/")
    token = str(getattr(settings, "home_assistant_token", "") or "").strip()
    return url, token


async def ha_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
) -> Any:
    """Chamada crua à API do HA. Levanta; quem trata é a tool."""
    url, token = _credenciais()
    if not url or not token:
        raise ValueError(_SEM_CREDENCIAL)

    import httpx

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.request(
            method,
            f"{url}{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code in (401, 403):
            msg = (
                "o Home Assistant recusou o token (HTTP "
                f"{resp.status_code}) — gere um novo Long-Lived Access Token "
                "no seu perfil e salve em Settings"
            )
            raise RuntimeError(msg)
        if resp.status_code >= 400:
            msg = f"Home Assistant respondeu {resp.status_code} em {path}"
            raise RuntimeError(msg)
        try:
            return resp.json()
        except ValueError as exc:
            # HA atrás de proxy/portal devolve HTML numa falha de auth.
            msg = f"resposta do Home Assistant em {path} não é JSON"
            raise RuntimeError(msg) from exc


@vtool(extras=ToolExtras(render_hint="table", category="smart_home", icon="home"))
async def ha_list_entities(
    domain: str = "",
) -> str:
    """Lista as entidades do Home Assistant, com o estado atual de cada uma.

    Args:
        domain: Filtra por domínio (`light`, `switch`, `sensor`, `lock`...).
            Vazio lista tudo.

    Returns:
        JSON com `entities`, ou com `error`.
    """
    try:
        estados = await ha_request("GET", "/api/states")
        if not isinstance(estados, list):
            return _erro("Home Assistant devolveu /api/states fora do formato de lista")

        prefixo = f"{domain.strip()}." if domain.strip() else ""
        entidades = [
            {
                "entity_id": e.get("entity_id"),
                "state": e.get("state"),
                "attributes": e.get("attributes") or {},
            }
            for e in estados
            if isinstance(e, dict) and str(e.get("entity_id", "")).startswith(prefixo)
        ]

        saida: dict[str, Any] = {"entities": entidades}
        if prefixo and not entidades:
            # Lista vazia sem aviso faria o LLM concluir que a casa não tem
            # nenhum dispositivo desse tipo — pode ser só o domínio errado.
            saida["warning"] = (
                f"nenhuma entidade no domínio `{domain}` — confira o nome do "
                "domínio listando sem filtro"
            )
        return json.dumps(saida, ensure_ascii=False)
    except Exception as exc:
        logger.exception("ha_list_entities: falha", extra={"domain": domain})
        return _erro(str(exc))


@vtool(extras=ToolExtras(render_hint="json", category="smart_home", icon="home"))
async def ha_get_state(
    entity_id: str,
) -> str:
    """Estado atual de uma entidade específica do Home Assistant.

    Args:
        entity_id: Id completo, no formato `dominio.nome` (ex.: `light.sala`).

    Returns:
        JSON com `state` e `attributes`, ou com `error`.
    """
    try:
        if not entity_id.strip():
            return _erro("entity_id vazio — informe algo como `light.sala`")

        try:
            estado = await ha_request("GET", f"/api/states/{entity_id.strip()}")
        except RuntimeError as exc:
            # 404 genérico faria o LLM concluir que o Home Assistant caiu.
            if "404" in str(exc):
                return _erro(
                    f"entidade `{entity_id}` não existe no Home Assistant — "
                    "use ha_list_entities pra ver os ids disponíveis"
                )
            raise

        if not isinstance(estado, dict):
            return _erro(f"resposta inesperada para `{entity_id}`")
        return json.dumps(
            {
                "entity_id": estado.get("entity_id"),
                "state": estado.get("state"),
                "attributes": estado.get("attributes") or {},
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("ha_get_state: falha", extra={"entity_id": entity_id})
        return _erro(str(exc))


@vtool(extras=ToolExtras(render_hint="table", category="smart_home", icon="home"))
async def ha_list_services(
    domain: str,
) -> str:
    """Serviços disponíveis num domínio do Home Assistant.

    Use antes de `ha_call_service` pra descobrir o nome exato do serviço em
    vez de adivinhar.

    Args:
        domain: Domínio a consultar (ex.: `light`, `lock`, `climate`).

    Returns:
        JSON com `services`, ou com `error`.
    """
    try:
        catalogo = await ha_request("GET", "/api/services")
        if not isinstance(catalogo, list):
            return _erro("Home Assistant devolveu /api/services fora do formato")

        alvo = domain.strip()
        for item in catalogo:
            if isinstance(item, dict) and item.get("domain") == alvo:
                return json.dumps(
                    {"domain": alvo, "services": sorted(item.get("services") or {})},
                    ensure_ascii=False,
                )
        return json.dumps(
            {
                "domain": alvo,
                "services": [],
                "warning": f"domínio `{alvo}` não existe nesta instância",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("ha_list_services: falha", extra={"domain": domain})
        return _erro(str(exc))


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="smart_home",
        destructive=True,
        icon="home",
    )
)
async def ha_call_service(
    domain: str,
    service: str,
    entity_id: str = "",
    data: dict | None = None,
) -> str:
    """Chama um serviço do Home Assistant — age nos dispositivos de verdade.

    Ação no mundo físico (ligar luz, destrancar porta, acionar alarme). Sempre
    pede aprovação humana antes de executar.

    Args:
        domain: Domínio do serviço (ex.: `light`).
        service: Nome do serviço (ex.: `turn_on`). Confirme com
            `ha_list_services` se não tiver certeza.
        entity_id: Entidade alvo. Vazio aplica ao domínio inteiro — cuidado.
        data: Parâmetros extras do serviço (ex.: `{"brightness": 120}`).

    Returns:
        JSON com `changed` (entidades afetadas), ou com `error`.
    """
    try:
        if not domain.strip() or not service.strip():
            return _erro("domain e service são obrigatórios")

        payload: dict[str, Any] = dict(data or {})
        if entity_id.strip():
            payload["entity_id"] = entity_id.strip()

        resultado = await ha_request(
            "POST",
            f"/api/services/{domain.strip()}/{service.strip()}",
            payload=payload,
        )
        mudadas = resultado if isinstance(resultado, list) else []
        return json.dumps(
            {"changed": mudadas, "called": f"{domain}.{service}"}, ensure_ascii=False
        )
    except Exception as exc:
        logger.exception(
            "ha_call_service: falha", extra={"domain": domain, "service": service}
        )
        return _erro(str(exc))


HOME_ASSISTANT_TOOLS: list[Any] = [
    ha_list_entities,
    ha_get_state,
    ha_list_services,
    ha_call_service,
]

__all__ = [
    "HOME_ASSISTANT_TOOLS",
    "ha_call_service",
    "ha_get_state",
    "ha_list_entities",
    "ha_list_services",
    "ha_request",
]
