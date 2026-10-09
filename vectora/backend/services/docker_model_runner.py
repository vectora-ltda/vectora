"""Executor local e cliente de descoberta do Docker Model Runner.

O processo ``docker`` só é iniciado pelo backend desktop protegido pela bridge.
Os argumentos são sempre passados como uma lista, sem shell, e a saída fica
limitada para impedir que o CLI transforme logs de download em memória sem
limite. O endpoint HTTP é usado apenas para descoberta e readiness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_DMR_BASE_URL: Final[str] = "http://127.0.0.1:12434"
MAX_OUTPUT_BYTES: Final[int] = 64 * 1024
COMMAND_TIMEOUT_SECONDS: Final[float] = 120.0
MODEL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,255}$")


@dataclass(frozen=True)
class DmrProbe:
    """Resultado normalizado das APIs locais do Model Runner."""

    reachable: bool
    contract: str | None
    models: tuple[str, ...]
    detail: str | None = None


def validate_model_reference(reference: str) -> str:
    """Valida uma referência Docker/OCI sem permitir argumentos adicionais."""
    value = reference.strip()
    if not MODEL_REFERENCE.fullmatch(value) or value.startswith("-"):
        raise ValueError("referência de modelo Docker inválida")
    return value


def normalize_base_url(value: str | None) -> str:
    """Aceita somente HTTP(S) e remove barras finais do endpoint local."""
    base = (value or DEFAULT_DMR_BASE_URL).strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint do Docker Model Runner inválido")
    return base


async def run_docker_model(
    *args: str, command_timeout: float = COMMAND_TIMEOUT_SECONDS
) -> str:
    """Executa uma operação fechada do plugin ``docker model``.

    O chamador fornece somente argumentos já validados; ``docker`` e ``model``
    são sempre prefixados aqui para impedir execução de comandos arbitrários.
    """
    if any("\x00" in arg for arg in args):
        raise ValueError("argumento Docker inválido")
    process = await asyncio.create_subprocess_exec(
        "docker",
        "model",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), command_timeout)
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError("operação Docker Model Runner excedeu o timeout") from exc
    output = (stdout + b"\n" + stderr)[:MAX_OUTPUT_BYTES]
    text = output.decode("utf-8", errors="replace")
    if process.returncode:
        raise RuntimeError(
            text.strip() or f"docker model saiu com {process.returncode}"
        )
    return text


async def probe_dmr(
    base_url: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DmrProbe:
    """Sonda o contrato OpenAI e depois o contrato Ollama do DMR."""
    base = normalize_base_url(base_url)
    async with httpx.AsyncClient(
        timeout=5, follow_redirects=False, transport=transport
    ) as client:
        try:
            response = await client.get(f"{base}/engines/v1/models")
            if response.is_success:
                payload = response.json()
                models = tuple(
                    str(item.get("id"))
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                )
                return DmrProbe(True, "openai", models)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            logger.debug("dmr: contrato OpenAI indisponível", exc_info=True)
        try:
            response = await client.get(f"{base}/api/tags")
            if response.is_success:
                payload = response.json()
                models = tuple(
                    str(item.get("name"))
                    for item in payload.get("models", [])
                    if isinstance(item, dict) and item.get("name")
                )
                return DmrProbe(True, "ollama", models)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            logger.debug("dmr: contrato Ollama indisponível", exc_info=True)
    return DmrProbe(False, None, (), "Docker Model Runner indisponível")


async def docker_model_available() -> tuple[bool, str | None]:
    """Verifica Docker e o plugin sem considerar um endpoint HTTP como prova."""
    try:
        output = await run_docker_model("version", command_timeout=10)
    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)
    return True, output[-1000:] or None


async def prepare_model(reference: str) -> str:
    """Baixa/prepara o modelo usando apenas o comando oficial do Docker."""
    return await run_docker_model("pull", validate_model_reference(reference))


async def stop_model(reference: str) -> str:
    """Solicita parada ao plugin, quando suportada pela versão instalada."""
    return await run_docker_model("stop", validate_model_reference(reference))


async def remove_model(reference: str) -> str:
    """Remove um modelo pelo identificador oficial, nunca por filesystem."""
    return await run_docker_model("rm", validate_model_reference(reference))
