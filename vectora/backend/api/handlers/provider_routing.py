"""Provider routing dinâmico de LLM (Ollama local, OpenRouter) — descoberta e registro.

Endpoints (exigem auth via middleware):
    GET    /provider-routing/ollama/models                  — descoberta via {base_url}/api/tags
    GET    /provider-routing/ollama/registered               — modelos Ollama registrados
    POST   /provider-routing/ollama/registered                — registra um modelo (tag)
    DELETE /provider-routing/ollama/registered/{model_id}     — remove
    GET    /provider-routing/llamacpp/models                  — descoberta OpenAI-compatible
    GET    /provider-routing/huggingface/models?q=           — catálogo público de modelos

    GET    /provider-routing/openrouter/status                — key configurada? (mascarada)
    POST   /provider-routing/openrouter/key                   — valida e salva a key
    DELETE /provider-routing/openrouter/key                    — remove a key
    GET    /provider-routing/openrouter/models?q=              — catálogo público (cache ~1h)
    GET    /provider-routing/openrouter/registered             — modelos OpenRouter registrados
    POST   /provider-routing/openrouter/registered              — registra um modelo (id)
    DELETE /provider-routing/openrouter/registered/{model_id}   — remove

    GET    /provider-routing/nine-router/status                — endpoint+key configurados? (key mascarada)
    POST   /provider-routing/nine-router/config                 — salva endpoint + key juntos
    DELETE /provider-routing/nine-router/config                 — remove os dois
    GET    /provider-routing/nine-router/models                 — descoberta via {base_url}/models
    GET    /provider-routing/nine-router/registered              — modelos 9Router registrados
    POST   /provider-routing/nine-router/registered               — registra um modelo (id "provider/model")
    DELETE /provider-routing/nine-router/registered/{model_id}    — remove

Ollama não exige API key — a UI popula o dropdown consultando /api/tags do
host configurado em vez de digitação livre (evita erro de digitação virar
falha silenciosa no chat). OpenRouter exige key (proxy pago multi-provider) —
validada contra /api/v1/auth/key antes de persistir. 9Router
(https://github.com/decolua/9router) é uma instância local do próprio
usuário — exige endpoint + key juntos (não há serviço/key fixo como o
OpenRouter), descobertos via {base_url}/models (endpoint OpenAI-compatible
padrão, sem validação prévia como a do OpenRouter — o proxy não expõe um
endpoint dedicado de "auth/key"). Em todos os casos,
`load_native_llm("ollama:<tag>" | "openrouter:<id>" | "nine_router:<id>")`
(backend/services/utils.py) já resolve o id dinâmico — este módulo só cuida
de descoberta/validação e da lista de modelos escolhida pelo usuário.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tarfile
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal, ParamSpec, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.handlers.admin import require_admin
from backend.settings import CapabilityState, settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-routing", tags=["provider-routing"])


def _require_provider_admin(request: Request) -> None:
    """Autoriza mutações de configuração e runtime do provider."""
    if os.getenv("VECTORA_AUTH_REQUIRED", "true").lower() in {"false", "0", "no"}:
        return
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    require_admin(user)


ProviderAdmin = Annotated[None, Depends(_require_provider_admin)]


def _require_desktop_bridge(
    token: Annotated[str | None, Header(alias="x-vectora-desktop-bridge")] = None,
) -> None:
    """Limita operações de binários e pesos ao executor desktop local.

    O processo Electron injeta o token efêmero no transporte IPC. Em modo de
    desenvolvimento sem autenticação, a guarda permanece desabilitada para
    preservar o backend local sem Electron; em qualquer instalação protegida,
    a ausência do token bloqueia a operação antes de tocar no filesystem.
    """
    expected = os.getenv("VECTORA_DESKTOP_BRIDGE_TOKEN", "")
    if not expected:
        if os.getenv("VECTORA_AUTH_REQUIRED", "true").lower() in {
            "false",
            "0",
            "no",
        }:
            return
        raise HTTPException(status_code=403, detail="ponte local não autorizada")
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="ponte local não autorizada")


DesktopBridge = Depends(_require_desktop_bridge)

_RuntimeP = ParamSpec("_RuntimeP")
_RuntimeR = TypeVar("_RuntimeR")


def _runtime_transition(  # noqa: UP047
    handler: Callable[_RuntimeP, Awaitable[_RuntimeR]],
) -> Callable[_RuntimeP, Awaitable[_RuntimeR]]:
    """Serializa alterações no manifesto e no marcador do runtime."""

    @wraps(handler)
    async def guarded(*args: _RuntimeP.args, **kwargs: _RuntimeP.kwargs) -> _RuntimeR:
        async with _runtime_transition_lock:
            return await handler(*args, **kwargs)

    return guarded


async def _get_http_client() -> AsyncIterator[Any]:
    """Dependency do client HTTP usado pelas chamadas de descoberta/catálogo.

    Injeção via FastAPI (`Depends`) em vez de `httpx.AsyncClient()` direto —
    testes trocam o client com `app.dependency_overrides`, resolvido pelo
    próprio FastAPI dentro do mesmo contexto async da request. Evita a classe
    de flake de `unittest.mock.patch("httpx.AsyncClient", ...)` em cima do
    `TestClient` (que despacha a app ASGI numa portal/thread própria — o
    patch pode não estar mais em vigor quando o handler roda, dependendo de
    timing do event loop; reproduzido só em CI Linux, nunca localmente).
    """
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        yield client


_DISCOVERY_TIMEOUT_S = 2.5
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_CATALOG_TTL_S = 3600
_download_locks: dict[str, asyncio.Lock] = {}
_download_locks_guard = asyncio.Lock()
_download_cancel_events: dict[str, asyncio.Event] = {}
_download_progress: dict[str, dict[str, int | str | None]] = {}
_runtime_transition_lock = asyncio.Lock()
_MAX_DOWNLOAD_BYTES = 100 * 1024**3
_GITHUB_RELEASE_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
_HUGGINGFACE_DOWNLOAD_SUFFIXES = (".huggingface.co", ".hf.co")


class OllamaModelInfo(BaseModel):
    name: str
    size: int | None = None
    modified_at: str | None = None


def _validate_redirect_host(
    response: Any, *, allowed: frozenset[str], suffixes: tuple[str, ...] = ()
) -> None:
    """Impede que um redirect transforme uma fonte oficial em proxy arbitrário."""
    host = getattr(getattr(response, "url", None), "host", None)
    if not isinstance(host, str):
        return
    normalized = host.lower().rstrip(".")
    if normalized not in allowed and not any(
        normalized.endswith(suffix) for suffix in suffixes
    ):
        raise HTTPException(
            status_code=502, detail="redirecionamento para origem não autorizada"
        )


class OllamaDiscoveryResponse(BaseModel):
    reachable: bool
    models: list[OllamaModelInfo]


class OpenAICompatibleDiscoveryResponse(BaseModel):
    reachable: bool
    models: list[dict[str, str]]


class LlamaCppConnectionTestResponse(BaseModel):
    status: str
    models: list[dict[str, str]] = []
    detail: str | None = None


class LlamaCppConfigRequest(BaseModel):
    base_url: str
    api_key: str = ""
    model: str = ""


class LlamaCppStatus(BaseModel):
    configured: bool
    base_url: str
    model: str
    masked: str
    mode: Literal["managed", "external"]


class LlamaCppModeRequest(BaseModel):
    mode: Literal["managed", "external"]


class DmrConfigRequest(BaseModel):
    """Endpoint local e modelo padrão do Docker Model Runner."""

    base_url: str = "http://127.0.0.1:12434"
    model: str = ""


class DmrModelRequest(BaseModel):
    """Referência Docker/OCI validada antes de chegar ao executor."""

    reference: str


class HuggingFaceDownloadRequest(BaseModel):
    repo_id: str
    filename: str
    revision: str = "main"
    sha256: str | None = None
    resume: bool = True


class HuggingFaceInstallRequest(BaseModel):
    """Seleção explícita dos artefatos que formam um modelo local."""

    repo_id: str
    revision: str = "main"
    filename: str
    mmproj_filename: str | None = None
    alias: str | None = None
    provider: Literal["ollama", "llamacpp"] = "llamacpp"
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    license: str | None = None
    architecture: str | None = None
    quantization: str | None = None
    context_length: int | str | None = None
    compatibility: str | None = None


class HuggingFaceStartRequest(BaseModel):
    """Modelo instalado e parâmetros do sidecar gerenciado."""

    repo_id: str
    filename: str | None = None
    host: str = "127.0.0.1"
    port: int = 18080
    alias: str | None = None
    ctx_size: int | None = None
    n_gpu_layers: int | None = None
    threads: int | None = None
    parallel: int | None = None
    jinja: bool = False


class LlamaCppInstallRequest(BaseModel):
    asset_url: str
    sha256: str | None = None


class LlamaCppRollbackRequest(BaseModel):
    runtime_id: str


class LlamaCppRetentionRequest(BaseModel):
    """Política de retenção para versões inativas do runtime."""

    keep: int = 2


class LlamaCppRuntimeRequest(BaseModel):
    path: str


class LlamaCppSidecarRequest(BaseModel):
    executable: str
    model: str
    host: str = "127.0.0.1"
    port: int = 18080
    alias: str | None = None
    mmproj: str | None = None
    ctx_size: int | None = None
    n_gpu_layers: int | None = None
    threads: int | None = None
    parallel: int | None = None
    jinja: bool = False


def _llamacpp_runtime_root() -> Path:
    return (settings.vectora_home / "tools" / "llama.cpp").resolve()


def _llamacpp_manifest_path() -> Path:
    return _llamacpp_runtime_root() / "runtime-manifest.json"


async def _validate_runtime_executable(files: list[str]) -> str:
    """Valida o binário oficial antes de publicar uma versão como ativa."""
    candidates = [
        Path(path)
        for path in files
        if Path(path).name.lower() in {"llama-server", "llama-server.exe"}
    ]
    if not candidates:
        raise ValueError("release não contém llama-server")
    executable = candidates[0]
    try:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except (OSError, TimeoutError) as exc:
        raise ValueError("llama-server não pôde ser executado") from exc
    if process.returncode != 0:
        raise ValueError("llama-server rejeitou a verificação de versão")
    return (stdout or stderr).decode(errors="replace").strip()[:500]


def _extract_llamacpp_archive(archive: Path, destination_root: Path) -> list[str]:
    """Extrai releases oficiais sem permitir escape de diretório."""
    extracted: list[str] = []

    def safe_target(name: str) -> Path:
        target = (destination_root / name).resolve()
        if destination_root.resolve() not in target.parents:
            raise ValueError("arquivo do runtime fora do diretório permitido")
        return target

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("release contém link simbólico")
                target = safe_target(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    output.write(source.read())
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    target.chmod(mode)
                extracted.append(str(target))
        return extracted
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            for member in members:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("release contém entrada não suportada")
                target = safe_target(member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("arquivo do runtime inválido")
                with source, target.open("wb") as output:
                    output.write(source.read())
                if member.mode:
                    target.chmod(member.mode & 0o777)
                extracted.append(str(target))
        return extracted
    return []


class RegisteredModel(BaseModel):
    id: str
    tag: str
    created_at: str


class RegisterModelRequest(BaseModel):
    tag: str


class ConfirmLlamaCppModelRequest(BaseModel):
    tag: str
    model_path: str
    runtime_id: str
    parameters: dict[str, str | int | float | bool | None] = {}


class LlamaCppModelConfirmation(BaseModel):
    tag: str
    model_path: str
    model_sha256: str
    runtime_id: str
    parameters: dict[str, str | int | float | bool | None]
    evidence_sha256: str
    confirmed_at: str


async def _get_db() -> Any:
    """Reusa a conexão SQLite do handler de threads (mesmo arquivo
    ~/.vectora/checkpoints.db) em vez de abrir outra."""
    from backend.api.handlers.threads import _get_db as _threads_db

    return await _threads_db()


# `table` nunca vem de input externo — só os 2 literais definidos abaixo
# (ollama_registered_models / openrouter_registered_models) — f-string é
# segura aqui, sem risco de SQL injection via request.
async def _ensure_registry_table(db: Any, table: str) -> None:
    await db.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id         TEXT PRIMARY KEY,
            tag        TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    await db.commit()


async def _ensure_llamacpp_confirmation_table(db: Any) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS llamacpp_model_confirmations (
            tag TEXT PRIMARY KEY,
            model_path TEXT NOT NULL,
            model_sha256 TEXT NOT NULL,
            runtime_id TEXT NOT NULL,
            parameters TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            confirmed_at TEXT NOT NULL
        )
        """
    )
    await db.commit()


async def _clear_llamacpp_confirmations(db: Any) -> None:
    await _ensure_llamacpp_confirmation_table(db)
    await db.execute("DELETE FROM llamacpp_model_confirmations")
    await db.commit()


async def _list_registered(table: str) -> list[RegisteredModel]:
    db = await _get_db()
    await _ensure_registry_table(db, table)
    async with db.execute(
        f"SELECT id, tag, created_at FROM {table} ORDER BY created_at"  # noqa: S608  # nosec B608
    ) as cur:
        rows = await cur.fetchall()
    return [RegisteredModel(id=r[0], tag=r[1], created_at=r[2]) for r in rows]


async def _register(table: str, tag: str) -> RegisteredModel:
    tag = tag.strip()
    if not tag:
        raise HTTPException(status_code=400, detail="tag vazia")

    db = await _get_db()
    await _ensure_registry_table(db, table)
    model_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    try:
        await db.execute(
            f"INSERT INTO {table} (id, tag, created_at) VALUES (?, ?, ?)",  # noqa: S608  # nosec B608
            (model_id, tag, created_at),
        )
        await db.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="modelo já registrado") from exc
        raise
    return RegisteredModel(id=model_id, tag=tag, created_at=created_at)


async def _unregister(table: str, model_id: str) -> None:
    db = await _get_db()
    await _ensure_registry_table(db, table)
    await db.execute(f"DELETE FROM {table} WHERE id = ?", (model_id,))  # noqa: S608  # nosec B608
    await db.commit()


@router.get("/ollama/models")
async def discover_ollama_models() -> OllamaDiscoveryResponse:
    """Consulta {OLLAMA_BASE_URL}/api/tags. Host fora do ar → reachable=False,
    nunca deixa a exceção subir como 500 (é esperado o host estar desligado)."""
    import httpx

    from backend.settings import settings

    base_url = (settings.ollama_base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.info(
            "provider_routing: Ollama em %s inacessível", base_url, exc_info=True
        )
        return OllamaDiscoveryResponse(reachable=False, models=[])

    models = [
        OllamaModelInfo(
            name=m["name"], size=m.get("size"), modified_at=m.get("modified_at")
        )
        for m in data.get("models", [])
        if m.get("name")
    ]
    return OllamaDiscoveryResponse(reachable=True, models=models)


@router.get("/ollama/registered")
async def list_registered_ollama_models() -> list[RegisteredModel]:
    return await _list_registered("ollama_registered_models")


@router.post("/ollama/registered")
async def register_ollama_model(
    body: RegisterModelRequest, _: ProviderAdmin
) -> RegisteredModel:
    return await _register("ollama_registered_models", body.tag)


@router.delete("/ollama/registered/{model_id}")
async def unregister_ollama_model(model_id: str, _: ProviderAdmin) -> dict[str, bool]:
    await _unregister("ollama_registered_models", model_id)
    return {"ok": True}


@router.get("/llamacpp/models")
@router.get("/llama-cpp/models", include_in_schema=False)
async def discover_llamacpp_models() -> OpenAICompatibleDiscoveryResponse:
    """Descobre modelos em um servidor llama.cpp OpenAI-compatible.

    O endpoint é configurável para permitir uma instalação externa; por
    padrão permanece em loopback. Falhas de conexão são estado normal da UI.
    """
    import httpx

    base_url = (settings.llamacpp_base_url or "http://127.0.0.1:18080/v1").rstrip("/")
    headers = (
        {"Authorization": f"Bearer {settings.llamacpp_api_key}"}
        if settings.llamacpp_api_key
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.info(
            "provider_routing: llama.cpp em %s inacessível", base_url, exc_info=True
        )
        return OpenAICompatibleDiscoveryResponse(reachable=False, models=[])

    models = [
        {"id": str(item["id"]), "name": str(item.get("name", item["id"]))}
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return OpenAICompatibleDiscoveryResponse(reachable=True, models=models)


@router.post("/llamacpp/test")
@router.post("/llama-cpp/test", include_in_schema=False)
async def test_llamacpp_connection() -> LlamaCppConnectionTestResponse:
    """Testa o contrato OpenAI-compatible sem expor credenciais."""
    import httpx

    base_url = (settings.llamacpp_base_url or "http://127.0.0.1:18080/v1").rstrip("/")
    headers = (
        {"Authorization": f"Bearer {settings.llamacpp_api_key}"}
        if settings.llamacpp_api_key
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError:
        return LlamaCppConnectionTestResponse(status="unreachable")
    if response.status_code in {401, 403}:
        return LlamaCppConnectionTestResponse(status="auth_error")
    if response.status_code >= 400:
        return LlamaCppConnectionTestResponse(status="incompatible")
    try:
        payload = response.json()
        data = payload["data"]
        if not isinstance(data, list):
            raise ValueError
        models = [
            {"id": str(item["id"]), "name": str(item.get("name", item["id"]))}
            for item in data
            if isinstance(item, dict) and item.get("id")
        ]
    except (ValueError, KeyError, TypeError):
        return LlamaCppConnectionTestResponse(status="incompatible")
    if not models:
        return LlamaCppConnectionTestResponse(status="empty")
    return LlamaCppConnectionTestResponse(status="ok", models=models)


async def list_registered_llamacpp_models() -> list[RegisteredModel]:
    """Modelos llama.cpp selecionados para o seletor global."""
    return await _list_registered("llamacpp_registered_models")


@router.get("/llamacpp/registered")
@router.get("/llama-cpp/registered", include_in_schema=False)
async def get_registered_llamacpp_models() -> list[RegisteredModel]:
    return await list_registered_llamacpp_models()


@router.post("/llamacpp/registered")
@router.post("/llama-cpp/registered", include_in_schema=False)
async def register_llamacpp_model(
    body: RegisterModelRequest,
    _: ProviderAdmin,
) -> RegisteredModel:
    return await _register("llamacpp_registered_models", body.tag)


@router.delete("/llamacpp/registered/{model_id}")
@router.delete("/llama-cpp/registered/{model_id}", include_in_schema=False)
async def unregister_llamacpp_model(
    model_id: str,
    _: ProviderAdmin,
) -> dict[str, bool]:
    await _unregister("llamacpp_registered_models", model_id)
    return {"ok": True}


@router.post("/llamacpp/models/confirm")
@router.post("/llama-cpp/models/confirm", include_in_schema=False)
async def confirm_llamacpp_model(
    body: ConfirmLlamaCppModelRequest,
    _: ProviderAdmin,
) -> LlamaCppModelConfirmation:
    """Registra compatibilidade somente após verificar o arquivo local."""
    if not body.tag.strip() or not re.fullmatch(r"[0-9a-f]{16}", body.runtime_id):
        raise HTTPException(status_code=400, detail="evidência do modelo inválida")
    model_path = Path(body.model_path).expanduser().resolve()
    models_root = (settings.vectora_home / "models").resolve()
    if models_root not in model_path.parents or not await asyncio.to_thread(
        model_path.is_file
    ):
        raise HTTPException(
            status_code=400, detail="modelo fora do armazenamento gerenciado"
        )
    runtime_manifest = _llamacpp_manifest_path()
    try:
        manifest = await _read_json_file_async(runtime_manifest)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409, detail="runtime não possui manifesto"
        ) from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=409, detail="manifesto de runtime inválido")
    if not any(
        isinstance(item, dict) and item.get("id") == body.runtime_id
        for item in manifest.get("runtimes", [])
    ):
        raise HTTPException(status_code=409, detail="runtime não está instalado")
    model_sha256 = await asyncio.to_thread(_sha256_file, model_path)
    serialized = json.dumps(body.parameters, sort_keys=True, separators=(",", ":"))
    evidence_sha256 = hashlib.sha256(
        f"{model_sha256}:{body.runtime_id}:{serialized}".encode()
    ).hexdigest()
    confirmed_at = datetime.now(UTC).isoformat()
    db = await _get_db()
    await _ensure_llamacpp_confirmation_table(db)
    await db.execute(
        """
        INSERT INTO llamacpp_model_confirmations
          (tag, model_path, model_sha256, runtime_id, parameters, evidence_sha256, confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
          model_path=excluded.model_path, model_sha256=excluded.model_sha256,
          runtime_id=excluded.runtime_id, parameters=excluded.parameters,
          evidence_sha256=excluded.evidence_sha256, confirmed_at=excluded.confirmed_at
        """,
        (
            body.tag.strip(),
            str(model_path),
            model_sha256,
            body.runtime_id,
            serialized,
            evidence_sha256,
            confirmed_at,
        ),
    )
    await db.commit()
    return LlamaCppModelConfirmation(
        tag=body.tag.strip(),
        model_path=str(model_path),
        model_sha256=model_sha256,
        runtime_id=body.runtime_id,
        parameters=body.parameters,
        evidence_sha256=evidence_sha256,
        confirmed_at=confirmed_at,
    )


@router.get("/llamacpp/models/confirmations")
@router.get("/llama-cpp/models/confirmations", include_in_schema=False)
async def list_llamacpp_model_confirmations() -> list[LlamaCppModelConfirmation]:
    db = await _get_db()
    await _ensure_llamacpp_confirmation_table(db)
    async with db.execute(
        "SELECT tag, model_path, model_sha256, runtime_id, parameters, evidence_sha256, confirmed_at "
        "FROM llamacpp_model_confirmations ORDER BY confirmed_at"
    ) as cur:
        rows = await cur.fetchall()
    try:
        manifest = await _read_json_file_async(_llamacpp_manifest_path())
        if not isinstance(manifest, dict):
            manifest = {"runtimes": []}
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {"runtimes": []}
    runtime_ids = {
        item.get("id")
        for item in manifest.get("runtimes", [])
        if isinstance(item, dict)
    }
    valid: list[LlamaCppModelConfirmation] = []
    for row in rows:
        model_path = Path(row[1])
        if not await asyncio.to_thread(model_path.is_file) or row[3] not in runtime_ids:
            await db.execute(
                "DELETE FROM llamacpp_model_confirmations WHERE tag = ?", (row[0],)
            )
            continue
        model_sha256 = await asyncio.to_thread(_sha256_file, model_path)
        if model_sha256 != row[2]:
            await db.execute(
                "DELETE FROM llamacpp_model_confirmations WHERE tag = ?", (row[0],)
            )
            continue
        valid.append(
            LlamaCppModelConfirmation(
                tag=row[0],
                model_path=row[1],
                model_sha256=row[2],
                runtime_id=row[3],
                parameters=json.loads(row[4]),
                evidence_sha256=row[5],
                confirmed_at=row[6],
            )
        )
    await db.commit()
    return valid


@router.get("/llamacpp/status")
@router.get("/llama-cpp/status", include_in_schema=False)
async def get_llamacpp_status() -> LlamaCppStatus:
    key = settings.llamacpp_api_key or ""
    configured_url = settings.llamacpp_base_url or "http://127.0.0.1:18080/v1"
    mode_value = os.getenv("LLAMACPP_MODE")
    mode: Literal["managed", "external"] = (
        mode_value
        if mode_value in {"managed", "external"}
        else (
            "managed" if configured_url.startswith("http://127.0.0.1") else "external"
        )
    )
    return LlamaCppStatus(
        configured=bool(settings.llamacpp_base_url) or mode == "managed",
        base_url=configured_url,
        model=settings.llamacpp_model or "",
        masked=(f"{key[:4]}…{key[-4:]}" if len(key) > 8 else ("••••" if key else "")),
        mode=mode,
    )


@router.post("/llamacpp/config")
@router.post("/llama-cpp/config", include_in_schema=False)
async def set_llamacpp_config(
    body: LlamaCppConfigRequest,
    _: ProviderAdmin,
) -> LlamaCppStatus:
    from urllib.parse import urlparse

    base_url = body.base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="base_url inválida")
    if len(base_url) > 500 or len(body.api_key) > 500 or len(body.model) > 300:
        raise HTTPException(status_code=400, detail="configuração excede o limite")
    object.__setattr__(settings, "llamacpp_base_url", base_url)
    api_key = body.api_key.strip() or settings.llamacpp_api_key
    object.__setattr__(settings, "llamacpp_api_key", api_key)
    model = body.model.strip()
    object.__setattr__(settings, "llamacpp_model", model or None)
    env_file = _env_file()
    _set_env_key(env_file, "LLAMACPP_BASE_URL", base_url)
    _set_env_key(env_file, "LLAMACPP_MODEL", model)
    if model:
        os.environ["LLAMACPP_MODEL"] = model
    else:
        os.environ.pop("LLAMACPP_MODEL", None)
    if api_key:
        _set_env_key(env_file, "LLAMACPP_API_KEY", api_key)
    return await get_llamacpp_status()


@router.delete("/llamacpp/config")
@router.delete("/llama-cpp/config", include_in_schema=False)
async def clear_llamacpp_config(
    _: ProviderAdmin,
) -> LlamaCppStatus:
    env_file = _env_file()
    for key in (
        "LLAMACPP_BASE_URL",
        "LLAMACPP_MODEL",
        "LLAMACPP_API_KEY",
        "LLAMACPP_MODE",
    ):
        _remove_env_key(env_file, key)
        os.environ.pop(key, None)
    object.__setattr__(settings, "llamacpp_base_url", None)
    object.__setattr__(settings, "llamacpp_api_key", None)
    object.__setattr__(settings, "llamacpp_model", None)
    return await get_llamacpp_status()


@router.post("/llamacpp/mode")
@router.post("/llama-cpp/mode", include_in_schema=False)
async def set_llamacpp_mode(
    body: LlamaCppModeRequest,
    _: ProviderAdmin,
) -> LlamaCppStatus:
    """Troca explicitamente entre endpoint externo e sidecar gerenciado."""
    if body.mode == "managed":
        from backend.services.llamacpp_sidecar import llamacpp_status

        status = llamacpp_status()
        if not status.get("running"):
            raise HTTPException(
                status_code=409,
                detail="inicie o sidecar e aguarde o readiness antes de ativar managed",
            )
    env_file = _env_file()
    _set_env_key(env_file, "LLAMACPP_MODE", body.mode)
    os.environ["LLAMACPP_MODE"] = body.mode
    return await get_llamacpp_status()


@router.get("/llamacpp/sidecar/status")
@router.get("/llama-cpp/sidecar/status", include_in_schema=False)
async def get_llamacpp_sidecar_status() -> dict[str, int | bool | str | None]:
    """Expõe apenas o estado do processo gerenciado pelo Vectora."""
    from backend.services.llamacpp_sidecar import llamacpp_status

    return llamacpp_status()


@router.get("/dmr/status")
async def get_dmr_status() -> dict[str, object]:
    """Detecta Docker Model Runner e o contrato HTTP disponível no host local."""
    from backend.services.docker_model_runner import (
        DEFAULT_DMR_BASE_URL,
        docker_model_available,
        probe_dmr,
    )

    cli_available, cli_detail = await docker_model_available()
    base_url = settings.dmr_base_url or DEFAULT_DMR_BASE_URL
    probe = await probe_dmr(base_url)
    return {
        "configured": bool(settings.dmr_base_url),
        "base_url": base_url,
        "model": settings.dmr_model or "",
        "cli_available": cli_available,
        "cli_detail": cli_detail,
        "reachable": probe.reachable if probe else False,
        "contract": probe.contract if probe else None,
        "models": list(probe.models) if probe else [],
        "detail": probe.detail if probe else "endpoint ainda não configurado",
    }


@router.post("/dmr/config")
async def set_dmr_config(body: DmrConfigRequest, _: ProviderAdmin) -> dict[str, object]:
    """Persiste apenas endpoint/modelo; operações Docker exigem a bridge."""
    from backend.services.docker_model_runner import normalize_base_url

    try:
        base_url = normalize_base_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    model = body.model.strip()
    if model:
        from backend.services.docker_model_runner import validate_model_reference

        try:
            model = validate_model_reference(model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    object.__setattr__(settings, "dmr_base_url", base_url)
    object.__setattr__(settings, "dmr_model", model or None)
    env_file = _env_file()
    _set_env_key(env_file, "DMR_BASE_URL", base_url)
    _set_env_key(env_file, "DMR_MODEL", model)
    os.environ["DMR_BASE_URL"] = base_url
    if model:
        os.environ["DMR_MODEL"] = model
    else:
        os.environ.pop("DMR_MODEL", None)
    return await get_dmr_status()


@router.post("/dmr/test")
async def test_dmr_connection() -> dict[str, object]:
    """Executa a detecção de CLI e o probe HTTP sem alterar estado."""
    return await get_dmr_status()


@router.get("/dmr/models")
async def list_dmr_models() -> dict[str, object]:
    """Lista modelos descobertos pelo contrato que o DMR anunciou."""
    status = await get_dmr_status()
    return {
        "reachable": status["reachable"],
        "contract": status["contract"],
        "models": status["models"],
    }


@router.post("/dmr/models/prepare", dependencies=[DesktopBridge])
async def prepare_dmr_model(
    body: DmrModelRequest, _: ProviderAdmin
) -> dict[str, object]:
    """Prepara um modelo pelo plugin local, sem executar shell arbitrário."""
    from backend.services.docker_model_runner import (
        prepare_model,
        validate_model_reference,
    )

    try:
        reference = validate_model_reference(body.reference)
        output = await prepare_model(reference)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    object.__setattr__(settings, "dmr_model", reference)
    env_file = _env_file()
    _set_env_key(env_file, "DMR_MODEL", reference)
    os.environ["DMR_MODEL"] = reference
    return {"status": "prepared", "reference": reference, "output": output[-2000:]}


@router.post("/dmr/models/start", dependencies=[DesktopBridge])
async def start_dmr_model(body: DmrModelRequest, _: ProviderAdmin) -> dict[str, object]:
    """Prepara o modelo e só o registra depois de readiness HTTP."""
    await prepare_dmr_model(body, None)
    status = await get_dmr_status()
    if not status["reachable"]:
        raise HTTPException(status_code=503, detail="DMR não está pronto")
    return {"status": "ready", "reference": body.reference, **status}


@router.post("/dmr/models/stop", dependencies=[DesktopBridge])
async def stop_dmr_model(body: DmrModelRequest, _: ProviderAdmin) -> dict[str, object]:
    """Pede parada ao Docker sem remover o modelo armazenado."""
    from backend.services.docker_model_runner import (
        stop_model,
        validate_model_reference,
    )

    try:
        reference = validate_model_reference(body.reference)
        output = await stop_model(reference)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "stopped", "reference": reference, "output": output[-2000:]}


@router.delete("/dmr/models/{reference:path}", dependencies=[DesktopBridge])
async def remove_dmr_model(
    reference: str, confirm: bool = False, _: ProviderAdmin = None
) -> dict[str, object]:
    """Remove o modelo somente quando a UI envia confirmação explícita."""
    if not confirm:
        raise HTTPException(status_code=400, detail="confirmação explícita necessária")
    from backend.services.docker_model_runner import (
        remove_model,
        validate_model_reference,
    )

    try:
        value = validate_model_reference(reference)
        output = await remove_model(value)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if settings.dmr_model == value:
        object.__setattr__(settings, "dmr_model", None)
        env_file = _env_file()
        _remove_env_key(env_file, "DMR_MODEL")
        os.environ.pop("DMR_MODEL", None)
    return {"status": "removed", "reference": value, "output": output[-2000:]}


@router.post("/llamacpp/sidecar/start", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/sidecar/start", include_in_schema=False, dependencies=[DesktopBridge]
)
async def start_llamacpp_sidecar(
    body: LlamaCppSidecarRequest,
    _: ProviderAdmin,
) -> dict[str, int | bool | str | None]:
    """Inicia llama-server local sem assumir ownership de servidores externos."""
    from backend.services.llamacpp_sidecar import start_llamacpp

    if body.host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=400, detail="sidecar deve usar loopback")
    if not 1024 <= body.port <= 65535:
        raise HTTPException(status_code=400, detail="porta inválida")
    try:
        await start_llamacpp(
            body.executable,
            body.model,
            host=body.host,
            port=body.port,
            alias=body.alias,
            mmproj=body.mmproj,
            ctx_size=body.ctx_size,
            n_gpu_layers=body.n_gpu_layers,
            threads=body.threads,
            parallel=body.parallel,
            jinja=body.jinja,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return await get_llamacpp_sidecar_status()


@router.post("/huggingface/start", dependencies=[DesktopBridge])
async def start_installed_huggingface_model(
    body: HuggingFaceStartRequest,
    _: ProviderAdmin,
) -> dict[str, int | bool | str | None]:
    """Inicia um modelo instalado usando o runtime llama.cpp ativo.

    O manifesto é a única fonte de caminhos: não aceitamos um executável ou
    peso arbitrário nesta rota. O serviço valida que ambos permanecem dentro
    das raízes gerenciadas e ``start_llamacpp`` só retorna após o endpoint
    OpenAI-compatible responder.
    """
    from backend.services.llamacpp_sidecar import start_llamacpp

    repo_id = _validate_hf_path(body.repo_id, field="repo_id")
    if repo_id.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo_id deve ser owner/model")
    if body.host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=400, detail="sidecar deve usar loopback")
    if not 1024 <= body.port <= 65535:
        raise HTTPException(status_code=400, detail="porta inválida")
    model_root = (settings.vectora_home / "models" / "huggingface" / repo_id).resolve()
    manifest_path = model_root / "model-manifest.json"
    try:
        manifest = await _read_json_file_async(manifest_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=404, detail="modelo instalado não encontrado"
        ) from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise HTTPException(status_code=422, detail="manifesto do modelo inválido")
    model_entry = next(
        (
            item
            for item in manifest["files"]
            if isinstance(item, dict)
            and item.get("role") == "model"
            and (body.filename is None or item.get("filename") == body.filename)
        ),
        None,
    )
    if not isinstance(model_entry, dict) or not isinstance(
        model_entry.get("filename"), str
    ):
        raise HTTPException(status_code=404, detail="peso principal não encontrado")
    model_path = (model_root / model_entry["filename"]).resolve()
    if model_root not in model_path.parents or not await asyncio.to_thread(
        model_path.is_file
    ):
        raise HTTPException(status_code=409, detail="peso principal indisponível")

    runtime_root = _llamacpp_runtime_root()
    active_path = runtime_root / "active-runtime"
    try:
        active_id = (
            await asyncio.to_thread(active_path.read_text, encoding="utf-8")
        ).strip()
        runtime_manifest = await _read_json_file_async(_llamacpp_manifest_path())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409, detail="runtime llama.cpp não instalado"
        ) from exc
    runtimes = (
        runtime_manifest.get("runtimes", [])
        if isinstance(runtime_manifest, dict)
        else []
    )
    active_runtime_entry = next(
        (
            item
            for item in runtimes
            if isinstance(item, dict) and item.get("id") == active_id
        ),
        None,
    )
    if not isinstance(active_runtime_entry, dict) or not isinstance(
        active_runtime_entry.get("directory"), str
    ):
        raise HTTPException(status_code=409, detail="runtime ativo inválido")
    runtime_root_resolved = (runtime_root / "versions").resolve()
    runtime_candidates = [active_runtime_entry]
    runtime_candidates.extend(
        item
        for item in sorted(
            (item for item in runtimes if isinstance(item, dict)),
            key=lambda item: str(item.get("installed_at", "")),
            reverse=True,
        )
        if item is not active_runtime_entry and item.get("id") != active_id
    )
    mmproj_entry = next(
        (
            item
            for item in manifest["files"]
            if isinstance(item, dict) and item.get("role") == "mmproj"
        ),
        None,
    )
    mmproj_path: Path | None = None
    if isinstance(mmproj_entry, dict) and isinstance(mmproj_entry.get("filename"), str):
        candidate = (model_root / mmproj_entry["filename"]).resolve()
        if model_root not in candidate.parents or not await asyncio.to_thread(
            candidate.is_file
        ):
            raise HTTPException(status_code=409, detail="mmproj indisponível")
        mmproj_path = candidate
    started_runtime_id = active_id
    last_runtime_error: RuntimeError | None = None
    executable: Path | None = None
    for candidate in runtime_candidates:
        candidate_id = candidate.get("id")
        candidate_directory = candidate.get("directory")
        if not isinstance(candidate_id, str) or not isinstance(
            candidate_directory, str
        ):
            continue
        runtime_dir = Path(candidate_directory).expanduser().resolve()
        if runtime_root_resolved not in runtime_dir.parents:
            continue
        candidate_executable = next(
            (
                path
                for path in await asyncio.to_thread(
                    lambda path=runtime_dir: list(path.rglob("*"))
                )
                if path.name.lower() in {"llama-server", "llama-server.exe"}
            ),
            None,
        )
        if candidate_executable is None or not await asyncio.to_thread(
            candidate_executable.is_file
        ):
            continue
        try:
            await start_llamacpp(
                candidate_executable,
                model_path,
                host=body.host,
                port=body.port,
                alias=body.alias,
                mmproj=mmproj_path,
                ctx_size=body.ctx_size,
                n_gpu_layers=body.n_gpu_layers,
                threads=body.threads,
                parallel=body.parallel,
                jinja=body.jinja,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            last_runtime_error = exc
            continue
        started_runtime_id = candidate_id
        executable = candidate_executable
        break
    if executable is None:
        detail = "llama-server não ficou pronto em nenhum runtime instalado"
        if last_runtime_error is not None:
            detail = str(last_runtime_error)
        raise HTTPException(status_code=502, detail=detail) from last_runtime_error
    if started_runtime_id != active_id:
        await _write_text_atomic(active_path, started_runtime_id)
    tag = body.alias or str(manifest.get("alias") or model_entry["filename"])
    parameters: dict[str, str | int | float | bool | None] = {
        "alias": body.alias,
        "mmproj": str(mmproj_path) if mmproj_path else None,
        "ctx_size": body.ctx_size,
        "n_gpu_layers": body.n_gpu_layers,
        "threads": body.threads,
        "parallel": body.parallel,
        "jinja": body.jinja,
    }
    try:
        try:
            await _register("llamacpp_registered_models", tag)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
        confirmation = await confirm_llamacpp_model(
            ConfirmLlamaCppModelRequest(
                tag=tag,
                model_path=str(model_path),
                runtime_id=started_runtime_id,
                parameters=parameters,
            ),
            None,
        )
    except Exception as exc:
        from backend.services.llamacpp_sidecar import stop_llamacpp

        await stop_llamacpp()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=502, detail="não foi possível confirmar o modelo iniciado"
        ) from exc
    manifest["confirmed"] = {
        "tag": confirmation.tag,
        "runtime_id": confirmation.runtime_id,
        "parameters": confirmation.parameters,
        "evidence_sha256": confirmation.evidence_sha256,
        "confirmed_at": confirmation.confirmed_at,
    }
    await _write_json_atomic(manifest_path, manifest)
    return {
        "running": True,
        "executable": str(executable),
        "model": str(model_path),
        "host": body.host,
        "port": body.port,
        "model_tag": tag,
        "confirmed": True,
    }


@router.post("/llamacpp/sidecar/stop", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/sidecar/stop", include_in_schema=False, dependencies=[DesktopBridge]
)
async def stop_llamacpp_sidecar(
    _: ProviderAdmin,
) -> dict[str, int | bool | str | None]:
    """Encerra somente o processo iniciado pela rota de start."""
    from backend.services.llamacpp_sidecar import stop_llamacpp

    await stop_llamacpp()
    return await get_llamacpp_sidecar_status()


@router.get("/huggingface/models")
async def search_huggingface_models(
    q: str = "llama.cpp", provider: str = "llamacpp"
) -> dict[str, list[dict[str, str]]]:
    """Pesquisa modelos públicos para Ollama ou llama.cpp.

    O endpoint só retorna metadados; pesos continuam sendo baixados pelo
    cliente diretamente da Hugging Face. O provider limita a busca aos
    formatos que o runtime selecionado consegue consumir.
    """
    import httpx

    if provider not in {"ollama", "llamacpp"}:
        raise HTTPException(status_code=400, detail="provider inválido")
    query = q.strip()[:120] or "llama.cpp"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://huggingface.co/api/models",
                params={"search": query, "filter": "gguf", "limit": 25},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.info(
            "provider_routing: catálogo Hugging Face indisponível", exc_info=True
        )
        return {"models": []}
    return {
        "models": [
            {
                "id": str(item.get("id", "")),
                "pipeline_tag": str(item.get("pipeline_tag", "")),
                "provider": provider,
                "downloads": str(item.get("downloads", 0)),
                "license": str(item.get("cardData", {}).get("license", "")),
                "architecture": str(item.get("library_name", "")),
                "quantization": next(
                    (
                        str(tag)
                        for tag in item.get("tags", [])
                        if re.fullmatch(r"q\d(?:_[a-z0-9]+)*", str(tag).lower())
                    ),
                    "",
                ),
                "context_length": str(
                    (item.get("cardData") or {}).get("context_length", "")
                ),
                "format": "GGUF"
                if "gguf" in {str(tag).lower() for tag in item.get("tags", [])}
                else "",
                "compatibility": (
                    "provável"
                    if "gguf" in {str(tag).lower() for tag in item.get("tags", [])}
                    else "não verificada"
                ),
            }
            for item in payload
            if isinstance(item, dict) and item.get("id")
        ]
    }


def _validate_hf_path(value: str, *, field: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > 300
        or ".." in value
        or "\\" in value
        or value.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", "."} for part in value.split("/"))
    ):
        raise HTTPException(status_code=400, detail=f"{field} inválido")
    return value


def _sha256_file(path: Path) -> str:
    """Calcula o hash de um artefato local fora do event loop."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download_source_metadata_path(path: Path) -> Path:
    """Retorna o sidecar de procedência associado a um arquivo baixado."""
    return path.with_name(f"{path.name}.source.json")


def _read_json_file(path: Path) -> object:
    """Lê JSON em uma thread para não bloquear o event loop."""
    return json.loads(path.read_text(encoding="utf-8"))


async def _read_json_file_async(path: Path) -> object:
    return await asyncio.to_thread(_read_json_file, path)


async def _write_json_atomic(path: Path, payload: object) -> None:
    """Persiste um manifesto sem deixar arquivos parcialmente escritos."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(temporary.write_text, serialized, "utf-8")
    await asyncio.to_thread(temporary.replace, path)


async def _write_text_atomic(path: Path, value: str) -> None:
    """Persiste marcadores pequenos sem expor conteúdo parcialmente escrito."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(temporary.write_text, value, "utf-8")
    await asyncio.to_thread(temporary.replace, path)


async def _download_lock(key: str) -> asyncio.Lock:
    """Retorna o lock por artefato, evitando downloads concorrentes iguais."""
    async with _download_locks_guard:
        return _download_locks.setdefault(key, asyncio.Lock())


async def _ensure_download_capacity(path: Path, expected: int | None) -> None:
    """Rejeita artefatos acima do limite ou sem espaço suficiente."""
    if expected is not None and expected > _MAX_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail="arquivo excede o limite permitido")
    if expected is None:
        return
    usage = await asyncio.to_thread(shutil.disk_usage, path)
    if usage.free < expected:
        raise HTTPException(
            status_code=507, detail="espaço insuficiente no dispositivo"
        )


@router.get("/huggingface/models/{repo_id:path}")
async def get_huggingface_model_metadata(repo_id: str) -> dict[str, object]:
    """Retorna metadados do repositório sem encaminhar pesos ao backend."""
    import httpx

    repo_id = _validate_hf_path(repo_id, field="repo_id")
    if repo_id.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo_id deve ser owner/model")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"https://huggingface.co/api/models/{repo_id}")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=404, detail="modelo não encontrado") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Hugging Face indisponível"
        ) from exc
    siblings = data.get("siblings", [])
    files = [
        {
            "rfilename": item.get("rfilename", ""),
            "size": item.get("size"),
            "sha256": (
                (item.get("lfs") or {}).get("sha256")
                if isinstance(item.get("lfs"), dict)
                else None
            ),
            "format": "GGUF"
            if str(item.get("rfilename", "")).lower().endswith(".gguf")
            else "",
        }
        for item in siblings
        if isinstance(item, dict) and item.get("rfilename")
    ]
    return {
        "id": data.get("id", repo_id),
        "revision": str(data.get("sha") or "main"),
        "license": (data.get("cardData") or {}).get("license"),
        "downloads": data.get("downloads", 0),
        "tags": data.get("tags", []),
        "architecture": data.get("library_name", ""),
        "quantization": next(
            (
                str(tag)
                for tag in data.get("tags", [])
                if re.fullmatch(r"q\d(?:_[a-z0-9]+)*", str(tag).lower())
            ),
            "",
        ),
        "context_length": (data.get("cardData") or {}).get("context_length"),
        "compatibility": (
            "provável"
            if any(
                str(item.get("rfilename", "")).lower().endswith(".gguf")
                for item in siblings
                if isinstance(item, dict)
            )
            else "não verificada"
        ),
        "files": files,
    }


@router.post("/huggingface/download", dependencies=[DesktopBridge])
async def download_huggingface_model(
    body: HuggingFaceDownloadRequest,
    request: Request,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Baixa um arquivo diretamente da Hugging Face para o armazenamento local."""
    import httpx

    repo_id = _validate_hf_path(body.repo_id, field="repo_id")
    filename = _validate_hf_path(body.filename, field="filename")
    revision = _validate_hf_path(body.revision, field="revision")
    if repo_id.count("/") != 1 or (
        body.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", body.sha256)
    ):
        raise HTTPException(status_code=400, detail="identificador ou sha256 inválido")
    destination_root = settings.vectora_home / "models" / "huggingface" / repo_id
    destination = (destination_root / filename).resolve()
    if destination_root.resolve() not in destination.parents:
        raise HTTPException(
            status_code=400, detail="arquivo fora do diretório permitido"
        )
    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial_metadata = partial.with_name(partial.name + ".meta")
    source_metadata = _download_source_metadata_path(destination)
    # O arquivo parcial é identificado pelo caminho, independentemente da
    # revisão. Assim o DELETE da UI consegue cancelar a mesma transferência
    # mesmo quando a revisão foi omitida ou mudou entre tentativas.
    download_key = f"{repo_id}/{filename}"
    lock = await _download_lock(download_key)
    # Registre o evento antes de esperar pelo lock: o DELETE precisa conseguir
    # cancelar uma transferência que ainda esteja aguardando a seção crítica.
    cancel_event = asyncio.Event()
    previous_event = _download_cancel_events.get(download_key)
    if previous_event is not None:
        raise HTTPException(status_code=409, detail="download já está em execução")
    _download_cancel_events[download_key] = cancel_event
    try:
        async with lock:
            _download_progress[download_key] = {
                "downloaded": 0,
                "total": None,
                "status": "starting",
            }
            metadata: dict[str, object] | None = None
            if await asyncio.to_thread(partial_metadata.is_file):
                try:
                    candidate_metadata = await _read_json_file_async(partial_metadata)
                    if isinstance(candidate_metadata, dict):
                        metadata = candidate_metadata
                except (OSError, json.JSONDecodeError):
                    metadata = None
            if await asyncio.to_thread(partial.exists) and (
                metadata is None
                or metadata.get("repo_id") != repo_id
                or metadata.get("filename") != filename
                or metadata.get("revision") != revision
                or metadata.get("sha256") != body.sha256
            ):
                await asyncio.to_thread(partial.unlink, missing_ok=True)
                await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
            await _write_json_atomic(
                partial_metadata,
                {
                    "repo_id": repo_id,
                    "filename": filename,
                    "revision": revision,
                    "sha256": body.sha256,
                },
            )
            offset = (
                (await asyncio.to_thread(partial.stat)).st_size
                if body.resume and await asyncio.to_thread(partial.exists)
                else 0
            )
            url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"
            digest = hashlib.sha256()
            downloaded = offset
            try:
                async with httpx.AsyncClient(
                    timeout=1800, follow_redirects=True
                ) as client:
                    headers = {"Range": f"bytes={offset}-"} if offset else {}
                    async with client.stream("GET", url, headers=headers) as response:
                        _validate_redirect_host(
                            response,
                            allowed=frozenset({"huggingface.co"}),
                            suffixes=_HUGGINGFACE_DOWNLOAD_SUFFIXES,
                        )
                        if offset and response.status_code == 200:
                            offset = 0
                        elif offset and response.status_code != 206:
                            raise ValueError("retomada rejeitada pelo servidor")
                        downloaded = offset
                        response.raise_for_status()
                        length = response.headers.get("content-length")
                        total = (
                            offset + int(length)
                            if length and length.isdigit()
                            else None
                        )
                        _download_progress[download_key] = {
                            "downloaded": offset,
                            "total": total,
                            "status": "downloading",
                        }
                        await _ensure_download_capacity(
                            destination_root,
                            (offset + int(length))
                            if length and length.isdigit()
                            else None,
                        )
                        if offset:
                            content_range = response.headers.get("content-range", "")
                            match = re.fullmatch(
                                r"bytes (\d+)-(\d+)/(\d+|\*)", content_range
                            )
                            if not match or int(match.group(1)) != offset:
                                raise ValueError("Content-Range inválido")

                            def _hash_existing() -> None:
                                with partial.open("rb") as existing:
                                    while chunk := existing.read(1024 * 1024):
                                        digest.update(chunk)

                            await asyncio.to_thread(_hash_existing)
                        with partial.open("ab" if offset else "wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                if cancel_event.is_set():
                                    raise asyncio.CancelledError
                                if await request.is_disconnected():
                                    raise asyncio.CancelledError
                                downloaded += len(chunk)
                                if downloaded > _MAX_DOWNLOAD_BYTES:
                                    raise HTTPException(
                                        status_code=413,
                                        detail="arquivo excede o limite permitido",
                                    )
                                digest.update(chunk)
                                await asyncio.to_thread(output.write, chunk)
                                _download_progress[download_key]["downloaded"] = (
                                    downloaded
                                )
                if cancel_event.is_set() or await request.is_disconnected():
                    raise asyncio.CancelledError
                await asyncio.to_thread(partial.replace, destination)
                await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
                await _write_json_atomic(
                    source_metadata,
                    {
                        "repo_id": repo_id,
                        "filename": filename,
                        "revision": revision,
                        "sha256": digest.hexdigest(),
                        "size": downloaded,
                    },
                )
                _download_progress[download_key]["status"] = "completed"
            except asyncio.CancelledError:
                _download_progress[download_key]["status"] = "cancelled"
                await asyncio.to_thread(partial.unlink, missing_ok=True)
                await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
                raise
            except HTTPException:
                _download_progress[download_key]["status"] = "failed"
                raise
            except httpx.TransportError as exc:
                # Um erro de transporte é retomável; preserve o .part para que a
                # próxima tentativa não recomece um arquivo GGUF de dezenas de GB.
                _download_progress[download_key]["status"] = "interrupted"
                raise HTTPException(
                    status_code=502, detail="falha no download da Hugging Face"
                ) from exc
            except Exception as exc:
                _download_progress[download_key]["status"] = "failed"
                await asyncio.to_thread(partial.unlink, missing_ok=True)
                await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
                await asyncio.to_thread(destination.unlink, missing_ok=True)
                await asyncio.to_thread(source_metadata.unlink, missing_ok=True)
                raise HTTPException(
                    status_code=502, detail="falha no download da Hugging Face"
                ) from exc
            actual = digest.hexdigest()
            if body.sha256 and actual.lower() != body.sha256.lower():
                await asyncio.to_thread(destination.unlink, missing_ok=True)
                await asyncio.to_thread(source_metadata.unlink, missing_ok=True)
                await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
                raise HTTPException(
                    status_code=422, detail="checksum sha256 incompatível"
                )
            return {
                "status": "downloaded",
                "path": str(destination),
                "files": [],
                "sha256": actual,
            }
    finally:
        if _download_cancel_events.get(download_key) is cancel_event:
            _download_cancel_events.pop(download_key, None)


@router.get("/huggingface/download/progress")
async def get_huggingface_download_progress(
    repo_id: str, filename: str, revision: str = "main"
) -> dict[str, int | str | None]:
    """Retorna progresso do download local sem expor credenciais."""
    repo_id = _validate_hf_path(repo_id, field="repo_id")
    filename = _validate_hf_path(filename, field="filename")
    revision = _validate_hf_path(revision, field="revision")
    if repo_id.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo_id deve ser owner/model")
    return _download_progress.get(
        f"{repo_id}/{filename}",
        {"downloaded": 0, "total": None, "status": "idle"},
    )


@router.post("/huggingface/install", dependencies=[DesktopBridge])
async def install_huggingface_model(
    body: HuggingFaceInstallRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Confirma um conjunto de pesos já baixado e cria seu manifesto local.

    O wizard baixa cada arquivo com ``/huggingface/download`` e só então
    chama esta rota. Assim o manifesto nunca aponta para um download parcial,
    e os arquivos permanecem no dispositivo do usuário.
    """
    repo_id = _validate_hf_path(body.repo_id, field="repo_id")
    revision = _validate_hf_path(body.revision, field="revision")
    filename = _validate_hf_path(body.filename, field="filename")
    mmproj = (
        _validate_hf_path(body.mmproj_filename, field="mmproj_filename")
        if body.mmproj_filename
        else None
    )
    if repo_id.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo_id deve ser owner/model")
    alias = body.alias.strip() if body.alias else ""
    if body.provider == "ollama" and not alias:
        raise HTTPException(
            status_code=400,
            detail="alias é obrigatório para instalar um modelo no Ollama",
        )
    if body.alias is not None and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", body.alias.strip()
    ):
        raise HTTPException(status_code=400, detail="alias inválido")
    root = (settings.vectora_home / "models" / "huggingface" / repo_id).resolve()
    selected = [filename, *([mmproj] if mmproj else [])]
    files: list[dict[str, object]] = []
    for selected_name in selected:
        path = (root / selected_name).resolve()
        if root not in path.parents or not await asyncio.to_thread(path.is_file):
            raise HTTPException(
                status_code=409,
                detail=f"arquivo ainda não foi baixado: {selected_name}",
            )
        source_metadata_path = _download_source_metadata_path(path)
        try:
            source_metadata = await _read_json_file_async(source_metadata_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"metadados de origem ausentes: {selected_name}",
            ) from exc
        if not isinstance(source_metadata, dict) or (
            source_metadata.get("repo_id") != repo_id
            or source_metadata.get("filename") != selected_name
            or source_metadata.get("revision") != revision
        ):
            raise HTTPException(
                status_code=409,
                detail=f"arquivo não pertence à revisão solicitada: {selected_name}",
            )
        digest = await asyncio.to_thread(_sha256_file, path)
        if source_metadata.get("sha256") != digest:
            raise HTTPException(
                status_code=409,
                detail=f"hash do arquivo mudou: {selected_name}",
            )
        stat_result = await asyncio.to_thread(path.stat)
        size = stat_result.st_size
        files.append(
            {
                "filename": selected_name,
                "path": str(path),
                "size": size,
                "sha256": digest,
                "revision": revision,
                "role": "mmproj" if selected_name == mmproj else "model",
            }
        )
    manifest = {
        "repo_id": repo_id,
        "publisher": repo_id.split("/", 1)[0],
        "revision": revision,
        "alias": body.alias.strip() if body.alias else filename,
        "license": body.license,
        "architecture": body.architecture,
        "quantization": body.quantization,
        "context_length": body.context_length,
        "compatibility": body.compatibility or "não verificada",
        "parameters": body.parameters,
        "files": files,
        "created_at": datetime.now(UTC).isoformat(),
        "source": f"https://huggingface.co/{repo_id}/tree/{revision}",
    }
    manifest_path = root / "model-manifest.json"
    temporary = manifest_path.with_suffix(".tmp")
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        temporary.write_text,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    await asyncio.to_thread(temporary.replace, manifest_path)
    if body.provider == "ollama":
        import httpx

        ollama_url = (settings.ollama_base_url or "http://127.0.0.1:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=1800) as client:
                response = await client.post(
                    f"{ollama_url}/api/create",
                    json={"name": alias, "modelfile": f"FROM {files[0]['path']}"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail="não foi possível importar o modelo no Ollama"
            ) from exc
    return {"status": "installed", "manifest": manifest}


@router.get("/huggingface/installed")
async def list_installed_huggingface_models() -> dict[str, list[dict[str, object]]]:
    """Lista somente manifestos locais completos, sem expor credenciais."""
    root = (settings.vectora_home / "models" / "huggingface").resolve()
    if not await asyncio.to_thread(root.is_dir):
        return {"models": []}
    models: list[dict[str, object]] = []
    manifest_paths = await asyncio.to_thread(
        lambda: list(root.rglob("model-manifest.json"))
    )
    for manifest_path in manifest_paths:
        try:
            content = await asyncio.to_thread(manifest_path.read_text, encoding="utf-8")
            payload = json.loads(content)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("files"), list):
            models.append(payload)
    return {"models": models}


@router.delete("/huggingface/download", dependencies=[DesktopBridge])
async def cancel_huggingface_download(
    repo_id: str,
    filename: str,
    _: ProviderAdmin,
    revision: str = "main",
) -> dict[str, bool | str]:
    """Cancela um download e remove o parcial somente após fechar o arquivo."""
    repo_id = _validate_hf_path(repo_id, field="repo_id")
    filename = _validate_hf_path(filename, field="filename")
    revision = _validate_hf_path(revision, field="revision")
    if repo_id.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo_id deve ser owner/model")
    root = (settings.vectora_home / "models" / "huggingface" / repo_id).resolve()
    partial = (root / (filename + ".part")).resolve()
    partial_metadata = partial.with_name(partial.name + ".meta")
    if root not in partial.parents:
        raise HTTPException(
            status_code=400, detail="arquivo fora do diretório permitido"
        )
    download_key = f"{repo_id}/{filename}"
    cancel_event = _download_cancel_events.get(download_key)
    if cancel_event is not None:
        cancel_event.set()
        _download_progress.setdefault(
            download_key,
            {"downloaded": 0, "total": None, "status": "starting"},
        )["status"] = "cancelling"
    lock = await _download_lock(download_key)
    async with lock:
        await asyncio.to_thread(partial.unlink, missing_ok=True)
        await asyncio.to_thread(partial_metadata.unlink, missing_ok=True)
    return {"ok": True, "status": "cancelled"}


@router.get("/llamacpp/releases")
@router.get("/llama-cpp/releases", include_in_schema=False)
async def list_llamacpp_releases() -> dict[str, list[dict[str, object]]]:
    """Lista releases oficiais, sem carregar artefatos no backend."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.github.com/repos/ggml-org/llama.cpp/releases",
                params={"per_page": 10},
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Vectora",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="GitHub indisponível") from exc
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()
    os_patterns = {
        "windows": ("win",),
        "darwin": ("mac", "osx", "darwin"),
        "linux": ("ubuntu", "linux"),
    }
    os_tokens = os_patterns.get(system, ())
    arch_tokens = (
        ("arm64", "aarch64")
        if "arm" in machine or "aarch64" in machine
        else ("x64", "x86_64", "amd64")
    )

    def asset_payload(asset: dict[str, object]) -> dict[str, object]:
        name = str(asset.get("name", ""))
        lowered = name.lower()
        recommended = bool(
            os_tokens
            and any(token in lowered for token in os_tokens)
            and any(token in lowered for token in arch_tokens)
            and not any(
                token in lowered
                for token in ("cuda", "vulkan", "rocm", "hip", "sycl", "opencl")
            )
        )
        return {
            "name": name,
            "url": asset.get("browser_download_url", ""),
            "size": asset.get("size", 0),
            "sha256": str(asset.get("digest", "")).removeprefix("sha256:"),
            "recommended": recommended,
        }

    return {
        "releases": [
            {
                "tag": item.get("tag_name", ""),
                "name": item.get("name", ""),
                "assets": [
                    {
                        **asset_payload(asset),
                    }
                    for asset in item.get("assets", [])
                    if asset.get("browser_download_url")
                ],
            }
            for item in payload
            if isinstance(item, dict) and not item.get("draft")
        ]
    }


@router.post("/llamacpp/install", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/install", include_in_schema=False, dependencies=[DesktopBridge]
)
@_runtime_transition
async def install_llamacpp_runtime(
    body: LlamaCppInstallRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Baixa um artefato de release oficial para o armazenamento do usuário."""
    from urllib.parse import urlparse

    import httpx

    parsed = urlparse(body.asset_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise HTTPException(
            status_code=400, detail="runtime deve vir do GitHub oficial"
        )
    if not parsed.path.startswith("/ggml-org/llama.cpp/releases/download/"):
        raise HTTPException(status_code=400, detail="release do llama.cpp inválida")
    if body.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", body.sha256):
        raise HTTPException(status_code=400, detail="sha256 inválido")
    from backend.services.llamacpp_sidecar import llamacpp_status

    if llamacpp_status().get("running"):
        raise HTTPException(
            status_code=409,
            detail="pare o sidecar antes de instalar ou substituir um runtime",
        )
    filename = Path(parsed.path).name
    if not filename or filename in {".", ".."} or len(filename) > 200:
        raise HTTPException(status_code=400, detail="nome do artefato inválido")
    destination_root = _llamacpp_runtime_root()
    staging_root = destination_root / ".staging"
    await asyncio.to_thread(staging_root.mkdir, parents=True, exist_ok=True)
    destination = (staging_root / filename).resolve()
    digest = hashlib.sha256()
    downloaded = 0
    try:
        async with httpx.AsyncClient(timeout=1800, follow_redirects=True) as client:
            async with client.stream("GET", body.asset_url) as response:
                _validate_redirect_host(response, allowed=_GITHUB_RELEASE_HOSTS)
                response.raise_for_status()
                length = response.headers.get("content-length")
                await _ensure_download_capacity(
                    destination_root,
                    int(length) if length and length.isdigit() else None,
                )
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > _MAX_DOWNLOAD_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail="arquivo excede o limite permitido",
                            )
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
    except HTTPException:
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        raise
    except Exception as exc:
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        raise HTTPException(
            status_code=502, detail="falha no download do runtime"
        ) from exc
    actual = digest.hexdigest()
    if body.sha256 and actual.lower() != body.sha256.lower():
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        raise HTTPException(status_code=422, detail="checksum sha256 incompatível")
    version_root: Path | None = None
    staging_version: Path | None = None
    try:
        runtime_id = actual[:16]
        version_root = (destination_root / "versions" / runtime_id).resolve()
        try:
            existing_manifest = await _read_json_file_async(_llamacpp_manifest_path())
        except (FileNotFoundError, json.JSONDecodeError):
            existing_manifest = {"runtimes": []}
        existing = (
            next(
                (
                    item
                    for item in existing_manifest.get("runtimes", [])
                    if isinstance(item, dict) and item.get("id") == runtime_id
                ),
                None,
            )
            if isinstance(existing_manifest, dict)
            else None
        )
        if existing is not None and version_root.is_dir():
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            return {"status": "installed", "path": str(version_root), **existing}
        staging_version = version_root.parent / f".{runtime_id}.{uuid.uuid4().hex}.tmp"
        await asyncio.to_thread(staging_version.mkdir, parents=True, exist_ok=False)
        files = await asyncio.to_thread(
            _extract_llamacpp_archive, destination, staging_version
        )
        runtime_version = await _validate_runtime_executable(files)
        archive_path = staging_version / filename
        await asyncio.to_thread(destination.replace, archive_path)
        if version_root.exists():
            raise FileExistsError("runtime já publicado")
        await asyncio.to_thread(staging_version.replace, version_root)
        files = [
            str(version_root / Path(file).relative_to(staging_version))
            for file in files
        ]
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        if staging_version is not None:
            await asyncio.to_thread(shutil.rmtree, staging_version, ignore_errors=True)
        raise HTTPException(
            status_code=422, detail="arquivo do runtime inválido"
        ) from exc
    manifest_path = _llamacpp_manifest_path()
    try:
        manifest = await _read_json_file_async(manifest_path)
        if not isinstance(manifest, dict):
            manifest = {"runtimes": []}
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {"runtimes": []}
    runtimes = [item for item in manifest.get("runtimes", []) if isinstance(item, dict)]
    runtimes = [item for item in runtimes if item.get("id") != runtime_id]
    runtimes.append(
        {
            "id": runtime_id,
            "asset": filename,
            "source": body.asset_url,
            "sha256": actual,
            "files": files,
            "directory": str(version_root),
            "version": runtime_version,
            "installed_at": datetime.now(UTC).isoformat(),
        }
    )
    await _write_json_atomic(manifest_path, {"runtimes": runtimes})
    await _write_text_atomic(destination_root / "active-runtime", runtime_id)
    return {
        "status": "installed" if files else "downloaded",
        "path": str(version_root),
        "files": files,
        "sha256": actual,
    }


@router.get("/llamacpp/runtime/status")
@router.get("/llama-cpp/runtime/status", include_in_schema=False)
async def llamacpp_runtime_status() -> dict[str, object]:
    """Informa runtimes locais sem inspecionar ou remover pesos.

    ``state`` torna explícita a diferença entre uma instalação gerenciada,
    uma configuração externa e um estado persistido que deixou de representar
    um processo vivo. Os campos antigos permanecem para compatibilidade.
    """
    from backend.services.llamacpp_sidecar import llamacpp_status

    root = _llamacpp_runtime_root()
    sidecar = llamacpp_status()
    if not await asyncio.to_thread(root.is_dir):
        external = (
            bool(settings.llamacpp_base_url)
            and os.getenv("LLAMACPP_MODE", "external") != "managed"
        )
        persisted_pid = sidecar.get("persisted_pid")
        state = (
            "stale"
            if sidecar.get("stale_state")
            else "unknown-process"
            if isinstance(persisted_pid, int)
            else "external"
            if external
            else "absent"
        )
        return {
            "installed": False,
            "path": None,
            "files": [],
            "runtimes": [],
            "active_runtime": None,
            "free_bytes": None,
            "state": state,
            "managed": not external,
            "external": external,
            "stale_state": bool(sidecar.get("stale_state")),
        }

    def _list_files() -> list[str]:
        return [str(path) for path in root.rglob("*") if path.is_file()]

    files = await asyncio.to_thread(_list_files)
    manifest_path = _llamacpp_manifest_path()
    try:
        payload = await _read_json_file_async(manifest_path)
        runtimes = payload.get("runtimes", []) if isinstance(payload, dict) else []
    except (FileNotFoundError, json.JSONDecodeError):
        runtimes = []
    active_path = root / "active-runtime"
    try:
        active = (
            await asyncio.to_thread(active_path.read_text, encoding="utf-8")
        ).strip()
    except FileNotFoundError:
        active = None
    disk_usage = await asyncio.to_thread(shutil.disk_usage, root)
    external = (
        bool(settings.llamacpp_base_url)
        and os.getenv("LLAMACPP_MODE", "external") != "managed"
    )
    if sidecar.get("stale_state"):
        state = "stale"
    elif sidecar.get("running"):
        state = "managed-running"
    elif isinstance(sidecar.get("persisted_pid"), int):
        state = "unknown-process"
    elif active and any(
        isinstance(item, dict) and item.get("id") == active for item in runtimes
    ):
        state = "managed-ready"
    elif external:
        state = "external"
    else:
        state = "unavailable"
    return {
        "installed": bool(files),
        "path": str(root),
        "files": files,
        "runtimes": runtimes,
        "active_runtime": active,
        "free_bytes": disk_usage.free,
        "state": state,
        "managed": not external,
        "external": external,
        "stale_state": bool(sidecar.get("stale_state")),
    }


@router.post("/llamacpp/runtime/update", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/runtime/update", include_in_schema=False, dependencies=[DesktopBridge]
)
async def update_llamacpp_runtime(
    body: LlamaCppInstallRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Instala uma nova versão sem remover modelos ou versões registradas."""
    from backend.services.llamacpp_sidecar import llamacpp_status

    if llamacpp_status()["running"]:
        raise HTTPException(
            status_code=409,
            detail="pare o sidecar antes de atualizar o runtime",
        )
    return await install_llamacpp_runtime(body, None)


@router.post("/llamacpp/runtime/rollback", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/runtime/rollback", include_in_schema=False, dependencies=[DesktopBridge]
)
@_runtime_transition
async def rollback_llamacpp_runtime(
    body: LlamaCppRollbackRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Ativa uma versão já instalada sem apagar a versão atual."""
    from backend.services.llamacpp_sidecar import llamacpp_status

    if llamacpp_status()["running"]:
        raise HTTPException(
            status_code=409,
            detail="pare o sidecar antes de reverter o runtime",
        )
    if not re.fullmatch(r"[0-9a-f]{16}", body.runtime_id):
        raise HTTPException(status_code=400, detail="runtime inválido")
    root = _llamacpp_runtime_root()
    runtime_dir = (root / "versions" / body.runtime_id).resolve()
    versions_root = (root / "versions").resolve()
    if versions_root not in runtime_dir.parents or not await asyncio.to_thread(
        runtime_dir.is_dir
    ):
        raise HTTPException(status_code=404, detail="runtime não encontrado")
    manifest_path = _llamacpp_manifest_path()
    try:
        manifest = await _read_json_file_async(manifest_path)
        if not isinstance(manifest, dict):
            manifest = {"runtimes": []}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="manifesto não encontrado") from exc
    runtimes = manifest.get("runtimes", [])
    if not any(
        isinstance(item, dict) and item.get("id") == body.runtime_id
        for item in runtimes
    ):
        raise HTTPException(status_code=404, detail="runtime não registrado")
    await _write_text_atomic(root / "active-runtime", body.runtime_id)
    return {"ok": True, "active_runtime": body.runtime_id}


@router.delete("/llamacpp/runtime/{runtime_id}", dependencies=[DesktopBridge])
@router.delete(
    "/llama-cpp/runtime/{runtime_id}",
    include_in_schema=False,
    dependencies=[DesktopBridge],
)
@_runtime_transition
async def remove_llamacpp_runtime_version(
    runtime_id: str,
    _: ProviderAdmin,
) -> dict[str, bool]:
    """Remove uma versão inativa sem afetar outras versões ou modelos."""
    import shutil

    from backend.services.llamacpp_sidecar import llamacpp_status

    if llamacpp_status()["running"]:
        raise HTTPException(status_code=409, detail="pare o sidecar antes de remover")
    if not re.fullmatch(r"[0-9a-f]{16}", runtime_id):
        raise HTTPException(status_code=400, detail="runtime inválido")
    root = _llamacpp_runtime_root()
    active_path = root / "active-runtime"
    active = None
    if await asyncio.to_thread(active_path.is_file):
        active = (
            await asyncio.to_thread(active_path.read_text, encoding="utf-8")
        ).strip()
    if active == runtime_id:
        raise HTTPException(status_code=409, detail="não remova o runtime ativo")
    version_dir = (root / "versions" / runtime_id).resolve()
    versions_root = (root / "versions").resolve()
    if versions_root not in version_dir.parents or not await asyncio.to_thread(
        version_dir.is_dir
    ):
        raise HTTPException(status_code=404, detail="runtime não encontrado")
    manifest_path = _llamacpp_manifest_path()
    try:
        manifest = await _read_json_file_async(manifest_path)
        if not isinstance(manifest, dict):
            manifest = {"runtimes": []}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="manifesto não encontrado") from exc
    runtimes = manifest.get("runtimes", [])
    if not any(
        isinstance(item, dict) and item.get("id") == runtime_id for item in runtimes
    ):
        raise HTTPException(status_code=404, detail="runtime não registrado")
    await asyncio.to_thread(shutil.rmtree, version_dir)
    await _write_json_atomic(
        manifest_path,
        {
            "runtimes": [
                item
                for item in runtimes
                if isinstance(item, dict) and item.get("id") != runtime_id
            ]
        },
    )
    return {"ok": True}


@router.post("/llamacpp/runtime/cleanup", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/runtime/cleanup", include_in_schema=False, dependencies=[DesktopBridge]
)
@_runtime_transition
async def cleanup_llamacpp_runtime_versions(
    body: LlamaCppRetentionRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Apaga somente versões inativas além da retenção solicitada."""
    from backend.services.llamacpp_sidecar import llamacpp_status

    if body.keep < 0 or body.keep > 50:
        raise HTTPException(status_code=400, detail="retenção inválida")
    if llamacpp_status()["running"]:
        raise HTTPException(status_code=409, detail="pare o sidecar antes de limpar")
    root = _llamacpp_runtime_root()
    manifest_path = _llamacpp_manifest_path()
    try:
        manifest = await _read_json_file_async(manifest_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="manifesto não encontrado") from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=422, detail="manifesto inválido")
    active = None
    active_path = root / "active-runtime"
    if await asyncio.to_thread(active_path.is_file):
        active = await asyncio.to_thread(active_path.read_text, "utf-8")
        active = active.strip()
    runtimes = [item for item in manifest.get("runtimes", []) if isinstance(item, dict)]
    inactive = [item for item in runtimes if item.get("id") != active]
    inactive.sort(key=lambda item: str(item.get("installed_at", "")), reverse=True)
    retained = inactive[: body.keep]
    removed: list[str] = []
    for item in inactive[body.keep :]:
        runtime_id = item.get("id")
        if not isinstance(runtime_id, str) or not re.fullmatch(
            r"[0-9a-f]{16}", runtime_id
        ):
            continue
        version_dir = (root / "versions" / runtime_id).resolve()
        versions_root = (root / "versions").resolve()
        if versions_root in version_dir.parents and await asyncio.to_thread(
            version_dir.is_dir
        ):
            await asyncio.to_thread(shutil.rmtree, version_dir)
            removed.append(runtime_id)
    await _write_json_atomic(
        manifest_path,
        {
            "runtimes": (
                [active_item]
                if (
                    active_item := next(
                        (item for item in runtimes if item.get("id") == active), None
                    )
                )
                else []
            )
            + retained
        },
    )
    return {
        "ok": True,
        "removed": removed,
        "retained": [item.get("id") for item in retained],
    }


@router.post("/llamacpp/runtime/test", dependencies=[DesktopBridge])
@router.post(
    "/llama-cpp/runtime/test", include_in_schema=False, dependencies=[DesktopBridge]
)
async def check_llamacpp_runtime(
    body: LlamaCppRuntimeRequest,
    _: ProviderAdmin,
) -> dict[str, object]:
    """Executa apenas ``--version`` no binário indicado pelo usuário."""
    executable = Path(body.path).expanduser().resolve()
    if not await asyncio.to_thread(
        executable.is_file
    ) or executable.name.lower() not in {
        "llama-server",
        "llama-server.exe",
    }:
        raise HTTPException(status_code=400, detail="binário llama-server inválido")
    try:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except (OSError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502, detail="não foi possível testar o runtime"
        ) from exc
    return {
        "ok": process.returncode == 0,
        "version": (stdout or stderr).decode(errors="replace").strip()[:500],
    }


@router.delete("/llamacpp/runtime", dependencies=[DesktopBridge])
@router.delete(
    "/llama-cpp/runtime", include_in_schema=False, dependencies=[DesktopBridge]
)
@_runtime_transition
async def remove_llamacpp_runtime(
    _: ProviderAdmin,
) -> dict[str, bool]:
    """Remove somente o runtime gerenciado; modelos ficam intactos."""
    import shutil

    from backend.services.llamacpp_sidecar import llamacpp_status

    if llamacpp_status()["running"]:
        raise HTTPException(
            status_code=409,
            detail="pare o sidecar antes de remover o runtime",
        )

    root = _llamacpp_runtime_root()
    if root.exists():
        await asyncio.to_thread(shutil.rmtree, root)
    await _clear_llamacpp_confirmations(await _get_db())
    return {"ok": True}


# ---------------------------------------------------------------------------
# OpenRouter — key (validada contra /auth/key), catálogo público, registro
# ---------------------------------------------------------------------------


class OpenRouterStatus(BaseModel):
    configured: bool
    masked: str


class OpenRouterKeyRequest(BaseModel):
    api_key: str


class OpenRouterModelInfo(BaseModel):
    id: str
    name: str
    context_length: int | None = None
    #: `architecture.input_modalities` da API do OpenRouter — inclui "image"
    #: só nos modelos que de fato processam imagem. Varia por modelo, não
    #: por ser servido via OpenRouter — daí não dar pra tratar "OpenRouter"
    #: como um bloco único vision-capable ou não.
    input_modalities: list[str] = []


class OpenRouterCatalogResponse(BaseModel):
    models: list[OpenRouterModelInfo]


def _env_file() -> Path:
    p = settings.vectora_home / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _set_env_key(env_file: Path, key: str, value: str) -> None:
    """Atualiza uma variável no .env sem duplicar linhas existentes."""
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    )
    prefix = f"{key}="
    filtered = [line for line in lines if not line.startswith(prefix)]
    if value:
        filtered.append(f"{prefix}{value}")
    env_file.write_text(
        "\n".join(filtered) + ("\n" if filtered else ""), encoding="utf-8"
    )


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:6]}•••{value[-4:]}"


def _remove_env_key(env_file: Path, key: str) -> None:
    if not env_file.exists():
        return
    lines = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if not line.startswith(f"{key}=")
    ]
    env_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


@router.get("/openrouter/status")
async def get_openrouter_status() -> OpenRouterStatus:
    import os

    raw = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return OpenRouterStatus(configured=bool(raw), masked=_mask_key(raw))


@router.post("/openrouter/key")
async def set_openrouter_key(
    body: OpenRouterKeyRequest, _: ProviderAdmin
) -> OpenRouterStatus:
    """Valida a key contra GET /auth/key antes de persistir — nunca salva uma
    key que a própria OpenRouter rejeita."""
    import os

    import httpx

    from backend.cli.keys import upsert_env_key

    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="key vazia")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_OPENROUTER_BASE_URL}/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Erro ao validar key na OpenRouter: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Key rejeitada pela OpenRouter (HTTP {resp.status_code})",
        )

    upsert_env_key(_env_file(), "OPENROUTER_API_KEY", api_key)
    os.environ["OPENROUTER_API_KEY"] = api_key
    from backend.settings import settings

    object.__setattr__(settings, "openrouter_api_key", api_key)
    invalidate_catalog_cache()

    return OpenRouterStatus(configured=True, masked=_mask_key(api_key))


@router.delete("/openrouter/key")
async def clear_openrouter_key(_: ProviderAdmin) -> OpenRouterStatus:
    import os

    from backend.settings import settings

    _remove_env_key(_env_file(), "OPENROUTER_API_KEY")
    os.environ.pop("OPENROUTER_API_KEY", None)
    object.__setattr__(settings, "openrouter_api_key", None)
    invalidate_catalog_cache()
    return OpenRouterStatus(configured=False, masked="")


# `fetched_at` usa `-inf`, não `0.0`, como sentinela de "nunca buscado":
# `time.monotonic()` não conta a partir de zero, reflete o uptime da
# máquina — numa VM efêmera de CI recém-bootada, `now` pode ser menor que
# `_OPENROUTER_CATALOG_TTL_S`, e `0.0` faria `now - fetched_at > TTL` dar
# falso (cache "resetado" pareceria recém-buscado, pulando o fetch e
# devolvendo lista vazia direto). `-inf` garante `now - fetched_at == +inf`
# sempre, não importa o uptime da máquina.
_catalog_cache: dict[str, Any] = {"fetched_at": float("-inf"), "models": []}

#: Catálogo mínimo usado quando a rede falha e o cache ainda está vazio.
#: Sem ele o seletor de modelo aparece sem nenhuma opção, o que o usuário lê
#: como falta de suporte a OpenRouter em vez de falta de internet. São ids
#: estáveis de modelos populares — a lista real vem da API assim que dá.
OPENROUTER_FALLBACK_MODELS: list[dict[str, Any]] = [
    {"id": "anthropic/claude-sonnet-4.5", "name": "Claude Sonnet 4.5"},
    {"id": "anthropic/claude-opus-4.1", "name": "Claude Opus 4.1"},
    {"id": "openai/gpt-4o", "name": "GPT-4o"},
    {"id": "openai/o3-mini", "name": "o3-mini"},
    {"id": "google/gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
    {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B Instruct"},
    {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1"},
    {"id": "qwen/qwen-2.5-72b-instruct", "name": "Qwen 2.5 72B Instruct"},
    {"id": "mistralai/mistral-large", "name": "Mistral Large"},
]


def _filtrar(models: list[OpenRouterModelInfo], q: str) -> list[OpenRouterModelInfo]:
    """Filtro por id/nome — compartilhado entre catálogo real e fallback."""
    needle = q.strip().lower()
    if not needle:
        return models
    return [m for m in models if needle in m.id.lower() or needle in m.name.lower()]


async def _ensure_openrouter_catalog_cached(client: Any) -> None:
    """Popula `_catalog_cache` se expirado — extraído de
    `discover_openrouter_models` pra ser reaproveitado por
    `openrouter_model_supports_image`, que precisa do catálogo fora do
    contexto de uma rota (sem `Depends`)."""
    now = time.monotonic()
    if now - _catalog_cache["fetched_at"] <= _OPENROUTER_CATALOG_TTL_S:
        return
    try:
        resp = await client.get(f"{_OPENROUTER_BASE_URL}/models")
        resp.raise_for_status()
        data = resp.json()
        _catalog_cache["models"] = [
            OpenRouterModelInfo(
                id=m["id"],
                name=m.get("name", m["id"]),
                context_length=m.get("context_length"),
                input_modalities=list(
                    m.get("architecture", {}).get("input_modalities") or []
                ),
            )
            for m in data.get("data", [])
            if m.get("id")
        ]
        _catalog_cache["fetched_at"] = now
    except Exception:
        logger.warning(
            "provider_routing: falha ao buscar catálogo OpenRouter", exc_info=True
        )


@router.get("/openrouter/models")
async def discover_openrouter_models(
    client: Annotated[Any, Depends(_get_http_client)], q: str = ""
) -> OpenRouterCatalogResponse:
    """Catálogo público de modelos da OpenRouter (não exige key). Cacheado em
    memória por _OPENROUTER_CATALOG_TTL_S — a lista muda pouco e evita bater
    na API a cada tecla digitada na busca do frontend."""
    await _ensure_openrouter_catalog_cached(client)
    if not _catalog_cache["models"]:
        # Sem rede e sem cache: a lista embutida é o que impede o seletor de
        # aparecer vazio, que o usuário lê como "o Vectora não suporta
        # OpenRouter" em vez de "estou sem internet". Não é gravada no
        # cache — a próxima tentativa tem que buscar o catálogo real, não se
        # dar por satisfeita com a lista curta.
        embutidos = [OpenRouterModelInfo(**m) for m in OPENROUTER_FALLBACK_MODELS]
        return OpenRouterCatalogResponse(models=_filtrar(embutidos, q)[:100])

    models: list[OpenRouterModelInfo] = _filtrar(_catalog_cache["models"], q)
    return OpenRouterCatalogResponse(models=models[:100])


async def openrouter_model_image_state(model_id: str) -> CapabilityState:
    """Capability real de visão do modelo `model_id` (ex.:
    "anthropic/claude-3.5-sonnet"), consultando o catálogo público
    cacheado — nunca trata "openrouter" como um bloco único com/sem visão,
    já que isso varia por modelo servido.

    Modelo ausente do catálogo (id incomum, catálogo indisponível e cache
    vazio) devolve ``UNKNOWN``. O modelo ativo pode ser tentado em modo
    fail-open; a cadeia de fallback deve excluir esse estado.
    """
    import httpx

    if not _catalog_cache["models"]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await _ensure_openrouter_catalog_cached(client)
        except Exception:
            logger.debug(
                "provider_routing: catálogo indisponível pra checar vision de %s",
                model_id,
            )

    for m in _catalog_cache["models"]:
        if m.id == model_id:
            modalities = _normalize_modalities(m.input_modalities)
            return (
                CapabilityState.SUPPORTED
                if {"image", "image_url", "vision"} & modalities
                else CapabilityState.UNSUPPORTED
            )
    return CapabilityState.UNKNOWN


async def openrouter_model_supports_image(model_id: str) -> bool:
    """Backward-compatible boolean wrapper; unknown remains fail-open."""
    return (
        await openrouter_model_image_state(model_id)
    ) is not CapabilityState.UNSUPPORTED


@router.get("/openrouter/registered")
async def list_registered_openrouter_models() -> list[RegisteredModel]:
    return await _list_registered("openrouter_registered_models")


@router.post("/openrouter/registered")
async def register_openrouter_model(
    body: RegisterModelRequest, _: ProviderAdmin
) -> RegisteredModel:
    return await _register("openrouter_registered_models", body.tag)


@router.delete("/openrouter/registered/{model_id}")
async def unregister_openrouter_model(
    model_id: str, _: ProviderAdmin
) -> dict[str, bool]:
    await _unregister("openrouter_registered_models", model_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 9Router (https://github.com/decolua/9router) — proxy local do usuário,
# integração leve (não absorvida como dependência nativa): endpoint + key +
# modelo, mesmo trio que o client espera. Diferente do OpenRouter, não há
# key/serviço fixo — os 2 campos (base_url + api_key) são interdependentes e
# salvos juntos num único request.
# ---------------------------------------------------------------------------


class NineRouterStatus(BaseModel):
    configured: bool
    base_url: str | None = None
    masked: str


class NineRouterConfigRequest(BaseModel):
    base_url: str
    api_key: str


class NineRouterModelInfo(BaseModel):
    id: str
    name: str
    input_modalities: list[str] = []


class NineRouterCatalogResponse(BaseModel):
    reachable: bool
    models: list[NineRouterModelInfo]


@router.get("/nine-router/status")
async def get_nine_router_status() -> NineRouterStatus:
    base_url = settings.nine_router_base_url
    api_key = settings.nine_router_api_key or ""
    return NineRouterStatus(
        configured=bool(base_url and api_key),
        base_url=base_url,
        masked=_mask_key(api_key),
    )


@router.post("/nine-router/config")
async def set_nine_router_config(
    body: NineRouterConfigRequest, _: ProviderAdmin
) -> NineRouterStatus:
    """Salva endpoint + key juntos — os dois são obrigatórios e
    interdependentes (diferente da key isolada do OpenRouter)."""
    import os

    from backend.cli.keys import upsert_env_key

    base_url = body.base_url.strip().rstrip("/")
    api_key = body.api_key.strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url vazia")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key vazia")

    upsert_env_key(_env_file(), "NINE_ROUTER_BASE_URL", base_url)
    upsert_env_key(_env_file(), "NINE_ROUTER_API_KEY", api_key)
    os.environ["NINE_ROUTER_BASE_URL"] = base_url
    os.environ["NINE_ROUTER_API_KEY"] = api_key
    object.__setattr__(settings, "nine_router_base_url", base_url)
    object.__setattr__(settings, "nine_router_api_key", api_key)
    invalidate_catalog_cache()

    return NineRouterStatus(
        configured=True, base_url=base_url, masked=_mask_key(api_key)
    )


@router.delete("/nine-router/config")
async def clear_nine_router_config(_: ProviderAdmin) -> NineRouterStatus:
    import os

    _remove_env_key(_env_file(), "NINE_ROUTER_BASE_URL")
    _remove_env_key(_env_file(), "NINE_ROUTER_API_KEY")
    os.environ.pop("NINE_ROUTER_BASE_URL", None)
    os.environ.pop("NINE_ROUTER_API_KEY", None)
    object.__setattr__(settings, "nine_router_base_url", None)
    object.__setattr__(settings, "nine_router_api_key", None)
    invalidate_catalog_cache()
    return NineRouterStatus(configured=False, base_url=None, masked="")


@router.get("/nine-router/models")
async def discover_nine_router_models(
    client: Annotated[Any, Depends(_get_http_client)],
) -> NineRouterCatalogResponse:
    """Descoberta via GET {base_url}/models (endpoint OpenAI-compatible
    padrão) — mesmo princípio anti-digitação-livre já usado pro Ollama.
    Proxy fora do ar ou não configurado → reachable=False, nunca 500."""
    base_url = settings.nine_router_base_url
    api_key = settings.nine_router_api_key
    if not base_url or not api_key:
        return NineRouterCatalogResponse(reachable=False, models=[])

    try:
        resp = await client.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.info(
            "provider_routing: 9Router em %s inacessível", base_url, exc_info=True
        )
        return NineRouterCatalogResponse(reachable=False, models=[])

    models = [
        NineRouterModelInfo(
            id=m["id"],
            name=m.get("name", m["id"]),
            input_modalities=list(
                m.get("architecture", {}).get("input_modalities")
                or m.get("input_modalities")
                or []
            ),
        )
        for m in data.get("data", [])
        if m.get("id")
    ]
    return NineRouterCatalogResponse(reachable=True, models=models)


_nine_router_catalog_cache: dict[str, Any] = {"fetched_at": float("-inf"), "models": []}
_CATALOG_FAILURE_BACKOFF_S = 30.0


def _normalize_modalities(values: list[str]) -> set[str]:
    return {value.strip().lower().replace("-", "_") for value in values}


async def nine_router_model_supports_image(model_id: str) -> CapabilityState:
    """Resolve image support from the configured Nine Router catalog."""
    import httpx

    now = time.monotonic()
    if now - _nine_router_catalog_cache["fetched_at"] > _OPENROUTER_CATALOG_TTL_S:
        base_url = settings.nine_router_base_url
        api_key = settings.nine_router_api_key
        if not base_url or not api_key:
            return CapabilityState.UNKNOWN
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                data = response.json()
            _nine_router_catalog_cache["models"] = [
                NineRouterModelInfo(
                    id=item["id"],
                    name=item.get("name", item["id"]),
                    input_modalities=list(
                        item.get("architecture", {}).get("input_modalities")
                        or item.get("input_modalities")
                        or []
                    ),
                )
                for item in data.get("data", [])
                if item.get("id")
            ]
            _nine_router_catalog_cache["fetched_at"] = now
        except Exception:
            logger.debug(
                "provider_routing: Nine Router catalog unavailable", exc_info=True
            )
            _nine_router_catalog_cache["fetched_at"] = (
                now - _OPENROUTER_CATALOG_TTL_S + _CATALOG_FAILURE_BACKOFF_S
            )

    for model in _nine_router_catalog_cache["models"]:
        if model.id == model_id:
            modalities = _normalize_modalities(model.input_modalities)
            return (
                CapabilityState.SUPPORTED
                if {"image", "image_url", "vision"} & modalities
                else CapabilityState.UNSUPPORTED
            )
    return CapabilityState.UNKNOWN


def invalidate_catalog_cache() -> None:
    """Invalidate provider model catalogs after credential/config changes."""
    _catalog_cache["fetched_at"] = float("-inf")
    _catalog_cache["models"] = []
    _nine_router_catalog_cache["fetched_at"] = float("-inf")
    _nine_router_catalog_cache["models"] = []


@router.get("/nine-router/registered")
async def list_registered_nine_router_models() -> list[RegisteredModel]:
    return await _list_registered("nine_router_registered_models")


@router.post("/nine-router/registered")
async def register_nine_router_model(
    body: RegisterModelRequest, _: ProviderAdmin
) -> RegisteredModel:
    return await _register("nine_router_registered_models", body.tag)


@router.delete("/nine-router/registered/{model_id}")
async def unregister_nine_router_model(
    model_id: str, _: ProviderAdmin
) -> dict[str, bool]:
    await _unregister("nine_router_registered_models", model_id)
    return {"ok": True}
