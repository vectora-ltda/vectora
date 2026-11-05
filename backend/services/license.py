"""Validação de licença Vectora.

Cada instalação comercial precisa de um ``VECTORA_TOKEN`` (gerado pelo dashboard
Vectora Company — ver ``docs/company.md``). O Launcher (``src/launcher.py``)
chama ``validate_license_async`` antes de subir qualquer subprocesso; o backend
expõe ``GET /license/status`` para o chat web mostrar trial banner / bloqueio.

Modelo de validação:

1. Lê ``VECTORA_TOKEN`` do env (override por ``--token`` no Launcher).
2. POSTa em ``${VECTORA_LICENSE_URL}`` (default: edge function Supabase
   ``validate-license``) com ``{token, vectora_version}``.
3. Resposta esperada: ``{tier: "free"|"pro", status: "active"|"trial"|...,
   days_remaining: int, expires_at: str}``.
4. Sucesso → cacheia em ``~/.vectora/license_cache.json`` (TTL 6h online,
   48h em modo offline graceful). Falha → tenta cache; se cache expirou,
   ``LicenseError``.

Sem ``VECTORA_TOKEN`` configurado → tier ``free`` direto (uso local solo, sem
conta) — **não é erro**. ``LicenseError`` só ocorre quando HÁ um token mas ele
é inválido/expirado/revogado, ou a validação remota falha sem cache utilizável.

**Auditoria**: cada validação grava em ``license_checks`` no Supabase.
Sem logging local — alinhado com "self-hosted no dado, centralizado na
licença".

**Modo dev/CI**: ``VECTORA_LICENSE_BYPASS=1`` pula a validação (uso interno
apenas; nunca documentar em produção).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

from backend.runtime_profile import resolve_runtime_home

logger = logging.getLogger(__name__)

LicenseTier = Literal["free", "pro"]
LicenseStatus = Literal["active", "trial", "expired", "revoked", "unknown"]

DEFAULT_LICENSE_URL = "https://services.vectora.company/license/validate"


def _bootstrap_vectora_home() -> Path:
    """Resolve a home sem importar o singleton de settings.

    O cache de licença permanece no mesmo perfil das demais partes do backend.
    """
    return resolve_runtime_home()


CACHE_PATH = _bootstrap_vectora_home() / "license_cache.json"
CONFIG_PATH = _bootstrap_vectora_home() / "config.toml"

CACHE_TTL_ONLINE = timedelta(hours=6)
CACHE_TTL_OFFLINE = timedelta(hours=48)
HTTP_TIMEOUT = 10.0


class LicenseError(RuntimeError):
    """Token inválido, expirado, revogado ou inexistente."""


@dataclass(frozen=True)
class LicenseStatusInfo:
    """Resultado da validação de licença."""

    tier: LicenseTier
    status: LicenseStatus
    days_remaining: int
    expires_at: str
    validated_at: str
    cached: bool

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "status": self.status,
            "days_remaining": self.days_remaining,
            "expires_at": self.expires_at,
            "validated_at": self.validated_at,
            "cached": self.cached,
        }


# ---------------------------------------------------------------------------
# Cache local
# ---------------------------------------------------------------------------


def _read_cache() -> dict | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("license: cache corrompido — ignorando")
        return None


def _write_cache(info: LicenseStatusInfo) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")


def clear_license_cache() -> None:
    """Remove o cache local — chamado ao trocar o token para que o status
    antigo não mascare a validação do token novo."""
    with contextlib.suppress(OSError):
        CACHE_PATH.unlink(missing_ok=True)


def _cache_is_fresh(payload: dict, ttl: timedelta) -> bool:
    try:
        validated_at = datetime.fromisoformat(payload["validated_at"])
    except (KeyError, ValueError):
        return False
    return datetime.now(UTC) - validated_at < ttl


# ---------------------------------------------------------------------------
# Validação remota
# ---------------------------------------------------------------------------


def load_token_from_config() -> str | None:
    """Lê o token persistido em ``~/.vectora/config.toml`` (``[license].token``).

    Fonte de verdade quando a env var não está setada — permite que o token
    salvo pela UI (setup wizard / admin) sobreviva a reinícios sem exigir
    export manual de ``VECTORA_TOKEN``.
    """
    if not CONFIG_PATH.is_file():
        return None
    try:
        import tomllib

        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("license: config.toml ilegível — ignorando")
        return None
    token = str(data.get("license", {}).get("token", "")).strip()
    return token or None


def _get_token() -> str | None:
    token = os.getenv("VECTORA_TOKEN", "").strip()
    if token:
        return token
    token = load_token_from_config()
    if token:
        # Espelha no environ para que consumidores existentes (ex: /license/portal)
        # continuem lendo de VECTORA_TOKEN.
        os.environ["VECTORA_TOKEN"] = token
        return token
    return None


def _get_license_url() -> str:
    return os.getenv("VECTORA_LICENSE_URL", DEFAULT_LICENSE_URL).strip()


def _vectora_version() -> str:
    """Versão atual — lida de ``importlib.metadata`` (sem ciclo de import)."""
    with contextlib.suppress(Exception):
        from importlib.metadata import version

        return version("vectora")
    return "0.0.0"


async def _validate_remote(token: str) -> LicenseStatusInfo:
    url = _get_license_url()
    payload = {"token": token, "vectora_version": _vectora_version()}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code == 401:
            raise LicenseError(
                "Token Vectora inválido. Verifique em "
                "https://vectora.company/dashboard."
            )
        if resp.status_code == 403:
            raise LicenseError(
                "Token revogado ou licença expirada. Renove em "
                "https://vectora.company/pricing."
            )
        resp.raise_for_status()
        data = resp.json()

    # A edge function `validate-license` responde 200 com
    # ``{valid: bool, reason, tier, status?, days_remaining?, expires_at?}``.
    # ``valid: false`` é falha de licença mesmo com HTTP 200 — sem este check
    # um token inexistente passava como "active" (bug do parsing antigo).
    if "valid" in data and not data["valid"]:
        reason = str(data.get("reason", "invalid"))
        if reason in ("not_found", "invalid"):
            raise LicenseError(
                "Token Vectora inválido. Verifique em "
                "https://vectora.company/dashboard."
            )
        raise LicenseError(
            "Licença expirada ou cancelada. Renove em https://vectora.company/pricing."
        )

    status = data.get("status")
    if status is None:
        # Compat com a resposta enxuta {valid, reason, tier}.
        status = "trial" if data.get("reason") == "trialing" else "active"
    return LicenseStatusInfo(
        tier=data.get("tier") or "free",
        status=status,
        days_remaining=int(data.get("days_remaining", 0)),
        expires_at=data.get("expires_at", "") or "",
        validated_at=datetime.now(UTC).isoformat(),
        cached=False,
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


async def validate_license_async(*, force: bool = False) -> LicenseStatusInfo:
    """Valida a licença e devolve o status atual.

    Política de cache:

    - Se a edge function respondeu recente (<6h) e a chamada falhou agora
      por network, devolve o cache (modo offline graceful, até 48h).
    - Se nada respondeu desde o boot e o cache é >48h, levanta
      ``LicenseError``.

    ``force=True`` ignora o cache fresco e revalida no servidor — usado ao
    salvar um token novo (setup wizard) e pelo loop periódico de 6h.

    ``VECTORA_LICENSE_BYPASS=1`` pula a validação inteira (modo dev).

    Sem ``VECTORA_TOKEN`` configurado, o Vectora roda no tier ``free``
    (uso local solo, sem conta) — não é mais erro. Só levanta
    ``LicenseError`` quando HÁ um token mas ele é inválido/expirado/
    revogado, ou quando a validação remota falha sem cache utilizável.
    """
    if os.getenv("VECTORA_LICENSE_BYPASS", "").strip() == "1":
        logger.info("license: bypass via VECTORA_LICENSE_BYPASS=1")
        return LicenseStatusInfo(
            tier="pro",
            status="active",
            days_remaining=365,
            expires_at=(datetime.now(UTC) + timedelta(days=365)).isoformat(),
            validated_at=datetime.now(UTC).isoformat(),
            cached=False,
        )

    token = _get_token()
    if not token:
        # Escreve no cache mesmo sem token: `get_current_tier()` (usado nos
        # gates de subscription) e `GET /license/status` leem via
        # `read_cached_status()` — sem isto, um usuário free que nunca
        # configurou token veria `tier=None` em vez de `free` até a primeira
        # tentativa de validação, quebrando o gating antes do primeiro boot.
        info = LicenseStatusInfo(
            tier="free",
            status="active",
            days_remaining=0,
            expires_at="",
            validated_at=datetime.now(UTC).isoformat(),
            cached=False,
        )
        _write_cache(info)
        return info

    cache = _read_cache()
    cache_fresh = (
        not force and cache is not None and _cache_is_fresh(cache, CACHE_TTL_ONLINE)
    )

    if cache_fresh and cache is not None:
        return LicenseStatusInfo(
            tier=cache.get("tier", "free"),
            status=cache.get("status", "active"),
            days_remaining=int(cache.get("days_remaining", 0)),
            expires_at=cache.get("expires_at", ""),
            validated_at=cache["validated_at"],
            cached=True,
        )

    try:
        info = await _validate_remote(token)
        _write_cache(info)
        return info
    except LicenseError:
        raise
    except Exception as exc:
        logger.warning("license: falha na validação remota (%s)", exc)
        # Cache graceful — aceita ate CACHE_TTL_OFFLINE quando offline.
        if cache is not None and _cache_is_fresh(cache, CACHE_TTL_OFFLINE):
            logger.info("license: usando cache offline (TTL 48h).")
            return LicenseStatusInfo(
                tier=cache.get("tier", "free"),
                status=cache.get("status", "active"),
                days_remaining=int(cache.get("days_remaining", 0)),
                expires_at=cache.get("expires_at", ""),
                validated_at=cache["validated_at"],
                cached=True,
            )
        raise LicenseError(
            "Falha ao validar licença e cache local expirou. Verifique sua "
            "conexão ou renove o token em https://vectora.company/dashboard."
        ) from exc


def validate_license_sync() -> LicenseStatusInfo:
    """Versão síncrona — usada pelo Launcher antes do uvicorn subir."""
    return asyncio.run(validate_license_async())


def _toml_value(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def write_config_section(
    section: str, values: dict[str, str | int | bool | None]
) -> None:
    """Persiste pares chave/valor em ``~/.vectora/config.toml`` (seção
    ``[<section>]``), preservando demais seções e chaves.

    Usado para ``[license]`` (token) e ``[server]`` (config admin) — todo
    estado de configuração do Vectora vive em SQLite/JSON/TOML, **nunca** em
    Postgres, mesmo no modo de armazenamento "complete" (ver
    ``src/storage/factory.py``).

    Valor ``None`` remove a chave (sem deletar a seção inteira).
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    raw = ""
    if CONFIG_PATH.is_file():
        raw = CONFIG_PATH.read_text(encoding="utf-8")

    lines = raw.splitlines()
    in_target = False
    found_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_target = stripped == f"[{section}]"
            new_lines.append(line)
            continue
        if in_target:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                found_keys.add(key)
                value = values[key]
                if value is not None:
                    new_lines.append(f"{key} = {_toml_value(value)}")
                # Valor None → linha removida.
                continue
        new_lines.append(line)

    pending = {k: v for k, v in values.items() if k not in found_keys and v is not None}
    if pending:
        if not any(line.strip() == f"[{section}]" for line in new_lines):
            if new_lines and new_lines[-1] != "":
                new_lines.append("")
            new_lines.append(f"[{section}]")
        for key, value in pending.items():
            new_lines.append(f"{key} = {_toml_value(value)}")

    CONFIG_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    # Permissão restritiva (apenas o owner lê) em Unix; no-op em Windows.
    with contextlib.suppress(OSError):
        CONFIG_PATH.chmod(0o600)


def write_token_to_config(token: str) -> None:
    """Persiste ``VECTORA_TOKEN`` em ``~/.vectora/config.toml`` (seção
    ``[license]``).

    O arquivo é lido em todo boot pelo launcher antes da validação remota;
    isto é, o operador pode trocar o token via UI sem precisar exportar
    a env var manualmente. Outras seções do TOML são preservadas.
    """
    write_config_section("license", {"token": token or None})


def read_cached_status() -> LicenseStatusInfo | None:
    """Lê o cache local sem validar remoto — usado pelo endpoint /license/status."""
    cache = _read_cache()
    if cache is None:
        return None
    return LicenseStatusInfo(
        tier=cache.get("tier", "free"),
        status=cache.get("status", "active"),
        days_remaining=int(cache.get("days_remaining", 0)),
        expires_at=cache.get("expires_at", ""),
        validated_at=cache.get("validated_at", ""),
        cached=True,
    )


def get_effective_storage_mode() -> str:
    """Retorna 'complete' apenas se o usuário tiver licença Pro configurada e storage_mode == 'complete'.
    Caso contrário, faz fallback seguro para 'lite'."""
    from backend.settings import settings

    if settings.storage_mode != "complete":
        return "lite"

    cache = _read_cache()
    tier = cache.get("tier", "free") if cache else "free"

    if tier == "pro":
        return "complete"

    logger.warning(
        "license: fallback para 'lite' (storage_mode='complete' requer licença Pro)"
    )
    return "lite"
