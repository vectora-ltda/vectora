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
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal
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


@dataclass
class DmrJob:
    """Estado observável de uma operação local do Model Runner."""

    id: str
    operation: Literal["prepare", "start"]
    reference: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    output: str = ""
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


_jobs: dict[str, DmrJob] = {}
_job_tasks: dict[str, asyncio.Task[None]] = {}


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


async def probe_dmr_inference(
    base_url: str | None,
    contract: str,
    model: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Confirma uma inferência mínima antes de declarar o DMR pronto."""
    base = normalize_base_url(base_url)
    if not model.strip() or contract not in {"openai", "ollama"}:
        return False
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=False, transport=transport
    ) as client:
        try:
            if contract == "openai":
                response = await client.post(
                    f"{base}/engines/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )
            else:
                response = await client.post(
                    f"{base}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
            return response.is_success
        except (httpx.HTTPError, ValueError):
            logger.debug("dmr: inferência de readiness falhou", exc_info=True)
            return False


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


async def run_model(reference: str) -> str:
    """Pré-carrega um modelo no runner sem abrir um chat interativo."""
    return await run_docker_model(
        "run", "--detach", validate_model_reference(reference)
    )


async def stop_model(reference: str) -> str:
    """Solicita parada ao plugin, quando suportada pela versão instalada."""
    return await run_docker_model("stop", validate_model_reference(reference))


async def remove_model(reference: str) -> str:
    """Remove um modelo pelo identificador oficial, nunca por filesystem."""
    return await run_docker_model("rm", validate_model_reference(reference))


async def inspect_model(reference: str) -> dict[str, object]:
    """Lê metadados fornecidos pelo Docker sem confiar em texto do usuário."""
    output = await run_docker_model("inspect", validate_model_reference(reference))
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"raw": output[-2000:]}
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        payload = payload[0]
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key]
        for key in ("name", "id", "digest", "size", "license", "engine", "backend")
        if key in payload and isinstance(payload[key], (str, int, float, bool))
    }


async def _run_job(job: DmrJob) -> None:
    """Executa um job e converte cancelamento em estado consultável."""
    job.status = "running"
    try:
        job.output = await prepare_model(job.reference)
        if job.operation == "start":
            job.output = (job.output + "\n" + await run_model(job.reference))[
                -MAX_OUTPUT_BYTES:
            ]
        try:
            job.metadata = await inspect_model(job.reference)
        except (OSError, RuntimeError, ValueError):
            job.metadata = {}
        job.status = "completed"
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.error = "operação cancelada"
    except (OSError, RuntimeError, ValueError) as exc:
        job.status = "failed"
        job.error = str(exc)[:2000]
    finally:
        _job_tasks.pop(job.id, None)


async def create_model_job(
    reference: str, operation: Literal["prepare", "start"]
) -> DmrJob:
    """Agenda uma operação local sem aceitar comandos fora do catálogo."""
    value = validate_model_reference(reference)
    job = DmrJob(
        id=uuid.uuid4().hex,
        operation=operation,
        reference=value,
        status="queued",
    )
    _jobs[job.id] = job
    _job_tasks[job.id] = asyncio.create_task(_run_job(job))
    return job


def get_model_job(job_id: str) -> DmrJob | None:
    """Retorna o estado de um job local ainda disponível nesta sessão."""
    return _jobs.get(job_id)


async def cancel_model_job(job_id: str) -> DmrJob | None:
    """Cancela o subprocesso associado ao job, se ele ainda estiver ativo."""
    job = _jobs.get(job_id)
    task = _job_tasks.get(job_id)
    if job is None:
        return None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return job
