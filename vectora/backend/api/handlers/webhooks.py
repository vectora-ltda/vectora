"""Handler de Webhooks — recebe e despacha eventos de provedores externos.

Infraestrutura genérica de recepção/verificação + handlers específicos por provider (GitHub, etc.).

Endpoints:
    POST /webhook/{provider}      — recebe evento, verifica assinatura, persiste e emite SSE
    POST /webhook/observability   — contrato genérico de alerta (Sentry, Grafana,
                                     PagerDuty, ...) → card do Kanban, sem parsing
                                     nativo de vendor (ver docs/content/guides/
                                     observability-webhooks.en.md)

Provedores suportados:
    github   — X-Hub-Signature-256 (HMAC-SHA256)
    gitlab   — X-Gitlab-Token (comparação direta)
    linear   — X-Linear-Signature (HMAC-SHA256)
    resend   — svix-signature (HMAC-SHA256)
    sendgrid — verificação ECDSA via chave pública
    mailgun  — token + timestamp + signature HMAC-SHA256

Configuração (env vars / Settings):
    GITHUB_WEBHOOK_SECRET
    GITLAB_WEBHOOK_SECRET
    LINEAR_WEBHOOK_SECRET
    RESEND_WEBHOOK_SECRET
    SENDGRID_WEBHOOK_KEY
    MAILGUN_WEBHOOK_SIGNING_KEY
    OBSERVABILITY_WEBHOOK_SECRET — secret do header X-Webhook-Secret
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

WebhookPayload = dict[str, Any]
WebhookHandler = Callable[[str, WebhookPayload, Request], Coroutine[Any, Any, None]]

# ---------------------------------------------------------------------------
# Verificação de assinatura por provider
# ---------------------------------------------------------------------------


def _verify_github(body: bytes, headers: dict[str, str], secret: str) -> bool:
    sig = headers.get("x-hub-signature-256", "")
    if not sig.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _verify_gitlab(body: bytes, headers: dict[str, str], secret: str) -> bool:
    token = headers.get("x-gitlab-token", "")
    return hmac.compare_digest(token, secret)


def _verify_linear(body: bytes, headers: dict[str, str], secret: str) -> bool:
    sig = headers.get("x-linear-signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _verify_resend(body: bytes, headers: dict[str, str], secret: str) -> bool:
    sig_header = headers.get("svix-signature", "")
    ts = headers.get("svix-timestamp", "")
    msg_id = headers.get("svix-id", "")
    signed_content = f"{msg_id}.{ts}.{body.decode()}"
    expected = hmac.new(
        secret.encode(), signed_content.encode(), hashlib.sha256
    ).hexdigest()
    import base64

    for part in sig_header.split(" "):
        if "," not in part:
            continue
        _, sig_b64 = part.split(",", 1)
        decoded = None
        with contextlib.suppress(Exception):
            decoded = base64.b64decode(sig_b64).hex()
        if decoded is not None and hmac.compare_digest(decoded, expected):
            return True
    return False


def _verify_mailgun(body: bytes, headers: dict[str, str], secret: str) -> bool:
    try:
        payload = json.loads(body)
        ts = str(payload.get("timestamp", ""))
        token = payload.get("token", "")
        signature = payload.get("signature", "")
    except Exception:
        return False
    data = (ts + token).encode()
    expected = hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


# mapa provider → (env var do secret, verificador)
_VERIFIERS: dict[str, tuple[str, Callable[[bytes, dict[str, str], str], bool]]] = {
    "github": ("GITHUB_WEBHOOK_SECRET", _verify_github),
    "gitlab": ("GITLAB_WEBHOOK_SECRET", _verify_gitlab),
    "linear": ("LINEAR_WEBHOOK_SECRET", _verify_linear),
    "resend": ("RESEND_WEBHOOK_SECRET", _verify_resend),
    "mailgun": ("MAILGUN_WEBHOOK_SIGNING_KEY", _verify_mailgun),
}

# ---------------------------------------------------------------------------
# Handlers específicos por provider
# ---------------------------------------------------------------------------


async def _handle_github(
    event_type: str, payload: WebhookPayload, request: Request
) -> None:
    """Processa eventos do GitHub e emite SSE para o workbench."""
    action = payload.get("action", "")
    normalized_type = f"{event_type}.{action}" if action else event_type

    # workflow_run — notifica CI status
    if event_type == "workflow_run":
        run = payload.get("workflow_run", {})
        _emit_sse_event(
            provider="github",
            event_type=normalized_type,
            data={
                "run_id": run.get("id"),
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
                "repo": payload.get("repository", {}).get("full_name"),
            },
        )

    # pull_request — atualiza aba Git
    elif event_type == "pull_request":
        pr = payload.get("pull_request", {})
        _emit_sse_event(
            provider="github",
            event_type=normalized_type,
            data={
                "pr_number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "merged": pr.get("merged"),
                "html_url": pr.get("html_url"),
                "repo": payload.get("repository", {}).get("full_name"),
            },
        )

    # push — novo commit no histórico
    elif event_type == "push":
        commits = payload.get("commits", [])
        _emit_sse_event(
            provider="github",
            event_type="push",
            data={
                "ref": payload.get("ref"),
                "commit_count": len(commits),
                "head_sha": payload.get("after"),
                "pusher": payload.get("pusher", {}).get("name"),
                "repo": payload.get("repository", {}).get("full_name"),
            },
        )

    # check_run — status de checks individuais
    elif event_type == "check_run":
        check = payload.get("check_run", {})
        _emit_sse_event(
            provider="github",
            event_type=normalized_type,
            data={
                "check_id": check.get("id"),
                "name": check.get("name"),
                "status": check.get("status"),
                "conclusion": check.get("conclusion"),
                "html_url": check.get("html_url"),
                "repo": payload.get("repository", {}).get("full_name"),
            },
        )

    # issues — atualiza Issues tab + sync determinístico no Kanban (sem LLM)
    elif event_type == "issues":
        issue = payload.get("issue", {})
        repo = payload.get("repository", {}).get("full_name")
        _emit_sse_event(
            provider="github",
            event_type=normalized_type,
            data={
                "issue_number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "html_url": issue.get("html_url"),
                "repo": repo,
            },
        )
        try:
            from backend.scheduling.background_tasks import sync_github_issue_to_kanban

            await sync_github_issue_to_kanban(action, repo or "", issue)
        except Exception:
            logger.exception("webhook: falha ao sincronizar issue com o kanban")


async def _handle_linear(
    event_type: str, payload: WebhookPayload, request: Request
) -> None:
    _emit_sse_event(
        provider="linear",
        event_type=event_type,
        data={
            "action": payload.get("action"),
            "type": payload.get("type"),
            "data": payload.get("data", {}),
        },
    )


async def _handle_gitlab(
    event_type: str, payload: WebhookPayload, request: Request
) -> None:
    _emit_sse_event(
        provider="gitlab",
        event_type=event_type,
        data={
            "object_kind": payload.get("object_kind"),
            "status": payload.get("object_attributes", {}).get("status"),
            "project": payload.get("project", {}).get("path_with_namespace"),
        },
    )


async def _handle_email(
    event_type: str, payload: WebhookPayload, request: Request
) -> None:
    _emit_sse_event(
        provider="email",
        event_type=event_type,
        data={
            "from": payload.get("from", payload.get("sender", "")),
            "to": payload.get("to", payload.get("recipient", "")),
            "subject": payload.get("subject", ""),
            "event": payload.get("event", event_type),
        },
    )


_HANDLERS: dict[str, WebhookHandler] = {
    "github": _handle_github,
    "gitlab": _handle_gitlab,
    "linear": _handle_linear,
    "resend": _handle_email,
    "sendgrid": _handle_email,
    "mailgun": _handle_email,
}
_SUPPORTED_PROVIDERS = frozenset(_VERIFIERS) | frozenset(_HANDLERS) | {"sendgrid"}

# ---------------------------------------------------------------------------
# SSE bridge — emite WebhookEvent para clientes conectados
#
# Cross-réplica: `_emit_sse_event` publica no KV (`CHANNEL_SSE` — Redis em
# modo complete, sidecar NATS por padrão, local em modo lite) em vez de
# escrever direto em `_sse_queues`. Cada réplica está inscrita nesse canal
# (registro em `backend/embedding/cache_sync.py::start_cache_sync`, que já é
# o bootstrap único de pub/sub do KV) e só ela entrega pros seus próprios
# clientes SSE via `_on_remote_sse_event` — sem isso, um evento de
# background_tasks/RAG processado na réplica A nunca chegava a um cliente
# conectado na réplica B (o `_sse_queues` sempre foi local ao processo).
# Em modo lite (MemoryKV), o publish entrega no mesmo processo — o
# comportamento observável não muda, só passa a existir a ponte pronta pra
# quando há mais de uma réplica.
# ---------------------------------------------------------------------------

CHANNEL_SSE = "vectora:sse"

# Fila global de eventos webhook SSE (asyncio.Queue por conexão aberta)
_sse_queues: list[Any] = []


def _emit_sse_event(provider: str, event_type: str, data: dict[str, Any]) -> None:
    from backend.persistence.kv import publish_soon

    event = {
        "type": "webhook_event",
        "provider": provider,
        "event_type": event_type,
        "data": data,
    }
    publish_soon(CHANNEL_SSE, json.dumps(event))


def on_remote_sse_event(payload: str) -> None:
    """Callback do KV — entrega um evento (desta réplica ou de outra) pros
    clientes SSE conectados NESTA réplica. Única gravadora de `_sse_queues`.
    """
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return
    for q in _sse_queues:
        with contextlib.suppress(Exception):
            q.put_nowait(event)


# ---------------------------------------------------------------------------
# Persistência no banco
# ---------------------------------------------------------------------------


async def _persist_event(
    provider: str,
    event_type: str,
    payload: WebhookPayload,
    workspace_id: str | None,
) -> None:
    try:
        from backend.rbac.auth import _get_db

        db = await _get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO webhook_events
                (id, provider, event_type, payload_json, workspace_id, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                str(uuid.uuid4()),
                provider,
                event_type,
                json.dumps(payload),
                workspace_id,
            ),
        )
        await db.commit()
    except Exception:
        logger.exception(
            "webhook: falha ao persistir evento provider=%s type=%s",
            provider,
            event_type,
        )


# ---------------------------------------------------------------------------
# Endpoint genérico de observabilidade — sem parsing nativo de vendor
#
# Contrato fixo: {title, description?, severity?, url?, external_id}. Ao
# contrário dos providers acima (assinatura HMAC própria de cada vendor),
# aqui a autenticação é um secret simples de header — não há uma assinatura
# de vendor específica pra verificar quando o "vendor" é qualquer ferramenta
# de alerta que o usuário aponte pra cá (Sentry, Grafana, PagerDuty, ...).
# Registrado ANTES de `/webhook/{provider}`: precisa vencer o path genérico
# na resolução de rotas do FastAPI, senão cairia no dispatcher sem o
# `X-Webhook-Secret` sendo verificado.
# ---------------------------------------------------------------------------


@router.post("/webhook/observability")
async def receive_observability_webhook(request: Request) -> Response:
    """Recebe um alerta de observabilidade genérico e sincroniza um card do
    Kanban — sem LLM no meio, mesmo caminho determinístico do sync de
    GitHub Issues (`sync_observability_alert_to_kanban`).

    Autenticação: header `X-Webhook-Secret` comparado (tempo constante) a
    `OBSERVABILITY_WEBHOOK_SECRET`. Sem secret configurado no servidor ou
    header ausente/incorreto, a requisição nunca chega a ser processada —
    diferente dos outros providers, aqui não há uma verificação HMAC de
    vendor pra usar como alternativa.
    """
    secret = os.environ.get("OBSERVABILITY_WEBHOOK_SECRET", "")
    received = request.headers.get("x-webhook-secret", "")
    if not secret or not hmac.compare_digest(received, secret):
        logger.warning("webhook: secret inválido/ausente provider=observability")
        raise HTTPException(status_code=401, detail="Secret inválido")

    body = await request.body()
    try:
        payload: WebhookPayload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload JSON inválido") from None

    title = payload.get("title")
    external_id = payload.get("external_id")
    if not title or not external_id:
        raise HTTPException(
            status_code=400,
            detail="payload requer 'title' e 'external_id'",
        )

    logger.info(
        "webhook: alerta de observabilidade recebido external_id=%s", external_id
    )
    await _persist_event("observability", "alert", payload, workspace_id=None)

    try:
        from backend.scheduling.background_tasks import (
            sync_observability_alert_to_kanban,
        )

        await sync_observability_alert_to_kanban(payload)
    except Exception:
        logger.exception("webhook: falha ao sincronizar alerta com o kanban")

    _emit_sse_event(
        provider="observability",
        event_type="alert",
        data={
            "title": title,
            "external_id": external_id,
            "severity": payload.get("severity"),
            "url": payload.get("url"),
        },
    )

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------


@router.post("/webhook/{provider}")
async def receive_webhook(provider: str, request: Request) -> Response:
    """Recebe webhook de um provider externo, verifica assinatura e despacha."""
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider de webhook não suportado")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    # Verificação de assinatura
    verifier_cfg = _VERIFIERS.get(provider)
    if verifier_cfg:
        env_var, verify_fn = verifier_cfg
        secret = os.environ.get(env_var, "")
        if secret and not verify_fn(body, headers, secret):
            logger.warning("webhook: assinatura inválida provider=%s", provider)
            raise HTTPException(status_code=401, detail="Assinatura inválida")

    # Parse do payload
    try:
        payload: WebhookPayload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload JSON inválido") from None

    # Determina tipo do evento. `payload["event"]` pode ser dict (Slack) OU
    # string (ex: email providers com {"event": "delivered"}) — só extrai
    # `.type` quando for dict.
    event_field = payload.get("event")
    nested_type = event_field.get("type") if isinstance(event_field, dict) else None
    event_type = (
        headers.get("x-github-event")
        or headers.get("x-gitlab-event")
        or payload.get("type")
        or nested_type
        or "unknown"
    )

    logger.info("webhook: recebido provider=%s event=%s", provider, event_type)

    # Persiste
    await _persist_event(provider, str(event_type), payload, workspace_id=None)

    # Despacha para handler específico
    handler = _HANDLERS.get(provider)
    if handler:
        try:
            await handler(str(event_type), payload, request)
        except Exception:
            logger.exception("webhook: erro no handler provider=%s", provider)

    # Ponte webhook→IA: dispara tasks 'webhook' cujo filtro casa este evento.
    # Fire-and-forget — a execução do agente pode demorar, e o provider (GitHub
    # etc.) espera resposta rápida; não bloqueamos o 200.
    async def _dispatch_bg() -> None:
        try:
            from backend.scheduling.background_tasks import dispatch_webhook_event

            await dispatch_webhook_event(provider, str(event_type), payload)
        except Exception:
            logger.exception("webhook: erro ao despachar para background tasks")

    import asyncio

    asyncio.create_task(_dispatch_bg())  # noqa: RUF006

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# SSE endpoint — frontend subscreve eventos webhook em tempo real
# ---------------------------------------------------------------------------


@router.get("/webhook/events")
async def webhook_events_stream(request: Request) -> Any:
    """SSE stream de eventos webhook recebidos em tempo real."""
    import asyncio

    from fastapi.responses import StreamingResponse

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
    _sse_queues.append(queue)

    async def _stream() -> Any:
        try:
            # Heartbeat inicial
            yield 'data: {"type":"connected","provider":"system"}\n\n'
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            _sse_queues.remove(queue)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
