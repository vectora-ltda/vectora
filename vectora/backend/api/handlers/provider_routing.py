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
import logging
import os
import re
import tarfile
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.settings import CapabilityState, settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provider-routing", tags=["provider-routing"])


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


class OllamaModelInfo(BaseModel):
    name: str
    size: int | None = None
    modified_at: str | None = None


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


class HuggingFaceDownloadRequest(BaseModel):
    repo_id: str
    filename: str
    revision: str = "main"
    sha256: str | None = None


class LlamaCppInstallRequest(BaseModel):
    asset_url: str
    sha256: str | None = None


class LlamaCppRuntimeRequest(BaseModel):
    path: str


class LlamaCppSidecarRequest(BaseModel):
    executable: str
    model: str
    host: str = "127.0.0.1"
    port: int = 8080


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
                extracted.append(str(target))
        return extracted
    return []


class RegisteredModel(BaseModel):
    id: str
    tag: str
    created_at: str


class RegisterModelRequest(BaseModel):
    tag: str


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
async def register_ollama_model(body: RegisterModelRequest) -> RegisteredModel:
    return await _register("ollama_registered_models", body.tag)


@router.delete("/ollama/registered/{model_id}")
async def unregister_ollama_model(model_id: str) -> dict:
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

    base_url = (settings.llamacpp_base_url or "http://127.0.0.1:8080/v1").rstrip("/")
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

    base_url = (settings.llamacpp_base_url or "http://127.0.0.1:8080/v1").rstrip("/")
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
async def register_llamacpp_model(body: RegisterModelRequest) -> RegisteredModel:
    return await _register("llamacpp_registered_models", body.tag)


@router.delete("/llamacpp/registered/{model_id}")
@router.delete("/llama-cpp/registered/{model_id}", include_in_schema=False)
async def unregister_llamacpp_model(model_id: str) -> dict[str, bool]:
    await _unregister("llamacpp_registered_models", model_id)
    return {"ok": True}


@router.get("/llamacpp/status")
@router.get("/llama-cpp/status", include_in_schema=False)
async def get_llamacpp_status() -> LlamaCppStatus:
    key = settings.llamacpp_api_key or ""
    return LlamaCppStatus(
        configured=bool(settings.llamacpp_base_url),
        base_url=settings.llamacpp_base_url or "http://127.0.0.1:8080/v1",
        model=settings.llamacpp_model or "",
        masked=(f"{key[:4]}…{key[-4:]}" if len(key) > 8 else ("••••" if key else "")),
    )


@router.post("/llamacpp/config")
@router.post("/llama-cpp/config", include_in_schema=False)
async def set_llamacpp_config(body: LlamaCppConfigRequest) -> LlamaCppStatus:
    from urllib.parse import urlparse

    base_url = body.base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="base_url inválida")
    if len(base_url) > 500 or len(body.api_key) > 500 or len(body.model) > 300:
        raise HTTPException(status_code=400, detail="configuração excede o limite")
    object.__setattr__(settings, "llamacpp_base_url", base_url)
    object.__setattr__(settings, "llamacpp_api_key", body.api_key.strip() or None)
    object.__setattr__(settings, "llamacpp_model", body.model.strip() or None)
    env_file = _env_file()
    _set_env_key(env_file, "LLAMACPP_BASE_URL", base_url)
    _set_env_key(env_file, "LLAMACPP_MODEL", body.model.strip())
    if body.api_key.strip():
        _set_env_key(env_file, "LLAMACPP_API_KEY", body.api_key.strip())
    else:
        _remove_env_key(env_file, "LLAMACPP_API_KEY")
    return await get_llamacpp_status()


@router.delete("/llamacpp/config")
@router.delete("/llama-cpp/config", include_in_schema=False)
async def clear_llamacpp_config() -> LlamaCppStatus:
    env_file = _env_file()
    for key in ("LLAMACPP_BASE_URL", "LLAMACPP_MODEL", "LLAMACPP_API_KEY"):
        _remove_env_key(env_file, key)
        os.environ.pop(key, None)
    object.__setattr__(settings, "llamacpp_base_url", None)
    object.__setattr__(settings, "llamacpp_api_key", None)
    object.__setattr__(settings, "llamacpp_model", None)
    return await get_llamacpp_status()


@router.get("/llamacpp/sidecar/status")
@router.get("/llama-cpp/sidecar/status", include_in_schema=False)
async def get_llamacpp_sidecar_status() -> dict[str, int | bool | None]:
    """Expõe apenas o estado do processo gerenciado pelo Vectora."""
    from backend.services.llamacpp_sidecar import llamacpp_status

    return llamacpp_status()


@router.post("/llamacpp/sidecar/start")
@router.post("/llama-cpp/sidecar/start", include_in_schema=False)
async def start_llamacpp_sidecar(
    body: LlamaCppSidecarRequest,
) -> dict[str, int | bool | None]:
    """Inicia llama-server local sem assumir ownership de servidores externos."""
    from backend.services.llamacpp_sidecar import start_llamacpp

    if body.host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=400, detail="sidecar deve usar loopback")
    if not 1024 <= body.port <= 65535:
        raise HTTPException(status_code=400, detail="porta inválida")
    try:
        await start_llamacpp(
            body.executable, body.model, host=body.host, port=body.port
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return await get_llamacpp_sidecar_status()


@router.post("/llamacpp/sidecar/stop")
@router.post("/llama-cpp/sidecar/stop", include_in_schema=False)
async def stop_llamacpp_sidecar() -> dict[str, int | bool | None]:
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
    if not value or len(value) > 300 or ".." in value or "\\" in value:
        raise HTTPException(status_code=400, detail=f"{field} inválido")
    return value


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
            "format": "GGUF"
            if str(item.get("rfilename", "")).lower().endswith(".gguf")
            else "",
        }
        for item in siblings
        if isinstance(item, dict) and item.get("rfilename")
    ]
    return {
        "id": data.get("id", repo_id),
        "license": (data.get("cardData") or {}).get("license"),
        "downloads": data.get("downloads", 0),
        "tags": data.get("tags", []),
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


@router.post("/huggingface/download")
async def download_huggingface_model(
    body: HuggingFaceDownloadRequest,
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(timeout=1800, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=502, detail="falha no download da Hugging Face"
        ) from exc
    actual = digest.hexdigest()
    if body.sha256 and actual.lower() != body.sha256.lower():
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="checksum sha256 incompatível")
    try:
        files = _extract_llamacpp_archive(destination, destination_root)
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail="arquivo do runtime inválido"
        ) from exc
    return {
        "status": "installed" if files else "downloaded",
        "path": str(destination),
        "files": files,
        "sha256": actual,
    }


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
    return {
        "releases": [
            {
                "tag": item.get("tag_name", ""),
                "name": item.get("name", ""),
                "assets": [
                    {
                        "name": asset.get("name", ""),
                        "url": asset.get("browser_download_url", ""),
                        "size": asset.get("size", 0),
                    }
                    for asset in item.get("assets", [])
                    if asset.get("browser_download_url")
                ],
            }
            for item in payload
            if isinstance(item, dict) and not item.get("draft")
        ]
    }


@router.post("/llamacpp/install")
@router.post("/llama-cpp/install", include_in_schema=False)
async def install_llamacpp_runtime(body: LlamaCppInstallRequest) -> dict[str, object]:
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
    filename = Path(parsed.path).name
    if not filename or filename in {".", ".."} or len(filename) > 200:
        raise HTTPException(status_code=400, detail="nome do artefato inválido")
    destination_root = (settings.vectora_home / "tools" / "llama.cpp").resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = (destination_root / filename).resolve()
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(timeout=1800, follow_redirects=True) as client:
            async with client.stream("GET", body.asset_url) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=502, detail="falha no download do runtime"
        ) from exc
    actual = digest.hexdigest()
    if body.sha256 and actual.lower() != body.sha256.lower():
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="checksum sha256 incompatível")
    return {"status": "downloaded", "path": str(destination), "sha256": actual}


@router.get("/llamacpp/runtime/status")
@router.get("/llama-cpp/runtime/status", include_in_schema=False)
async def llamacpp_runtime_status() -> dict[str, object]:
    """Informa runtimes locais sem inspecionar ou remover pesos."""
    root = (settings.vectora_home / "tools" / "llama.cpp").resolve()
    if not root.is_dir():
        return {"installed": False, "path": None, "files": []}
    files = [str(path) for path in root.rglob("*") if path.is_file()]
    return {"installed": bool(files), "path": str(root), "files": files}


@router.post("/llamacpp/runtime/test")
@router.post("/llama-cpp/runtime/test", include_in_schema=False)
async def test_llamacpp_runtime(body: LlamaCppRuntimeRequest) -> dict[str, object]:
    """Executa apenas ``--version`` no binário indicado pelo usuário."""
    executable = Path(body.path).expanduser().resolve()
    if not executable.is_file() or executable.name.lower() not in {
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


@router.delete("/llamacpp/runtime")
@router.delete("/llama-cpp/runtime", include_in_schema=False)
async def remove_llamacpp_runtime() -> dict[str, bool]:
    """Remove somente o runtime gerenciado; modelos ficam intactos."""
    import shutil

    root = (settings.vectora_home / "tools" / "llama.cpp").resolve()
    if root.exists():
        await asyncio.to_thread(shutil.rmtree, root)
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
async def set_openrouter_key(body: OpenRouterKeyRequest) -> OpenRouterStatus:
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
async def clear_openrouter_key() -> OpenRouterStatus:
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
async def register_openrouter_model(body: RegisterModelRequest) -> RegisteredModel:
    return await _register("openrouter_registered_models", body.tag)


@router.delete("/openrouter/registered/{model_id}")
async def unregister_openrouter_model(model_id: str) -> dict:
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
async def set_nine_router_config(body: NineRouterConfigRequest) -> NineRouterStatus:
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
async def clear_nine_router_config() -> NineRouterStatus:
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
async def register_nine_router_model(body: RegisterModelRequest) -> RegisteredModel:
    return await _register("nine_router_registered_models", body.tag)


@router.delete("/nine-router/registered/{model_id}")
async def unregister_nine_router_model(model_id: str) -> dict:
    await _unregister("nine_router_registered_models", model_id)
    return {"ok": True}
