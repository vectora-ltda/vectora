"""Helpers compartilhados do stream de chat: classificação de erro, checkpoint
de rewind por turno, marcação de thread com conteúdo real e anotação de
aprovação inteligente. Consumidos por ``backend/api/native_stream.py::
stream_engine_events`` — a origem real do stream de ``StreamChat``/
``ResumeChat`` (motor nativo, ``backend/engine/conversation_loop.py``).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def classify_stream_error(exc: BaseException) -> tuple[str, str]:
    """Classifica uma exceção do stream em ``(code, message)``.

    O ``code`` é tipado e estável para o frontend localizar a mensagem ao
    usuário (i18n no cliente). O ``message`` é um resumo limpo (sem o JSON cru
    do provedor) usado como fallback.

    Códigos: ``RATE_LIMIT`` (429 / quota esgotada), ``MISSING_KEYS`` (chave de
    API não configurada), ``AUTH`` (chave inválida / 401 / 403),
    ``MODEL_INCOMPATIBLE`` (provider rejeitou o histórico da conversa em
    todos os candidatos da cadeia de fallback — não é quota),
    ``RECURSION_LIMIT`` (o agente estourou o limite de passos sem convergir),
    ``STREAM_ERROR`` (genérico).
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    # MISSING_KEYS antes de AUTH: a falta de chave cita "api key" (que casaria
    # com AUTH). O GetEnvError pode chegar cru ou embrulhado num AttributeError.
    if (
        "getenverror" in text
        or "coheremissingerror" in text
        or ("env variable" in text and "does not exist" in text)
    ):
        return "MISSING_KEYS", "Configure suas chaves de API para usar o Vectora."
    # Checa a causa raiz encadeada ANTES do match genérico de "quota":
    # QuotaExhaustedError é levantado `from last_exc` quando a cadeia de
    # fallback se esgota — se TODOS os candidatos falharam pela MESMA
    # incompatibilidade de provider (ex.: um modelo novo da Cohere que
    # rejeita `tool_plan`), a palavra "quota" aparece na mensagem mas é
    # falsa: não é limite de uso, é o histórico da conversa incompatível
    # com o schema do modelo.
    from backend.llm.provider_fallback import is_provider_incompatible_error

    cause = exc.__cause__
    if is_provider_incompatible_error(exc) or (
        cause is not None and is_provider_incompatible_error(cause)
    ):
        return (
            "MODEL_INCOMPATIBLE",
            "Este modelo não conseguiu processar o histórico desta conversa.",
        )
    # HTTP 402 = conta sem crédito/membership no provedor (ex.
    # OpenRouterCreditError). Não é falha transitória nem quota do modelo —
    # é estado de conta; tenta fallback esconderia o problema. Estado de
    # conta roda ANTES do match genérico de rate limit, porque a mensagem
    # do 402 pode conter "rate" e colidir com RATE_LIMIT.
    from backend.llm.openrouter.client import OpenRouterCreditError

    if isinstance(exc, OpenRouterCreditError) or (
        cause is not None and isinstance(cause, OpenRouterCreditError)
    ):
        return (
            "ACCOUNT_CREDIT",
            "O provedor reportou falta de crédito ou assinatura ativa para este modelo.",
        )
    if any(
        (
            "402" in text,
            "credit" in text and "error" in text,
            "membership benefits" in text,
            "invalid_request_error" in text and "membership" in text,
        )
    ):
        return (
            "ACCOUNT_CREDIT",
            "O provedor reportou falta de crédito ou assinatura ativa para este modelo.",
        )
    keyword_matches: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (
            "RATE_LIMIT",
            "O limite de uso deste modelo foi atingido.",
            (
                "429",
                "too many requests",
                "resource_exhausted",
                "rate limit",
                "ratelimit",
                "quota",
            ),
        ),
        (
            "TIMEOUT",
            "A conexão com o modelo expirou. Tente novamente.",
            ("timeout", "timed out", "connecttimeout", "readtimeout"),
        ),
        (
            "AUTH",
            "Falha de autenticação com o provedor do modelo.",
            (
                "401",
                "403",
                "unauthorized",
                "permission denied",
                "api key",
                "api_key",
                "invalid authentication",
            ),
        ),
        (
            "RECURSION_LIMIT",
            (
                "O agente entrou em um loop e foi interrompido automaticamente. "
                "Tente novamente ou simplifique o pedido."
            ),
            ("graphrecursionerror", "recursion limit"),
        ),
    )
    for code, message, keywords in keyword_matches:
        if any(keyword in text for keyword in keywords):
            return code, message
    return "STREAM_ERROR", "Ocorreu um erro ao gerar a resposta."


async def _record_turn_checkpoint(
    workspace_id: str, thread_id: str, event: Any
) -> None:
    """Grava um artefato de checkpoint de rewind após cada turno do orchestrador.

    Chamado ao fim de cada turno completo do agente. Best-effort: qualquer
    falha (workspace sem git, I/O, banco indisponível) é registrada em log e
    descartada silenciosamente.
    """
    import uuid
    from datetime import UTC, datetime

    try:
        from backend.workspace.workspace import workspace_registry

        ws = workspace_registry.get(workspace_id)
        if ws is None:
            return

        import git as gitpy

        checkpoint_id = event.get("run_id") or str(uuid.uuid4())
        msg = f"turn:{thread_id}:{checkpoint_id[:8]}"

        strategy: str
        git_sha: str | None = None
        snapshot_path: str | None = None
        files_touched: str = "[]"

        try:
            repo = gitpy.Repo(ws.cwd, search_parent_directories=True)
            from backend.persistence.checkpoint import create_git_checkpoint

            result = create_git_checkpoint(repo, thread_id, msg)
            if result["status"] != "ok":
                logger.warning(
                    "_record_turn_checkpoint: git snapshot falhou: %s", result
                )
                return
            strategy = "git"
            git_sha = result["sha"]
        except (gitpy.InvalidGitRepositoryError, gitpy.NoSuchPathError):
            # Fallback: snapshot tarball para workspaces sem git.
            # NoSuchPathError ocorre quando o diretório do workspace não existe
            # ainda em disco (sessão nova não inicializada) — nesse caso não há
            # snapshot possível e retornamos silenciosamente.
            from pathlib import Path as _Path

            if not _Path(ws.cwd).exists():
                return

            from backend.persistence.checkpoint import (
                create_snapshot_checkpoint,
                gc_snapshots,
            )
            from backend.settings import settings

            snap_dir = settings.vectora_home / "snapshots" / workspace_id
            result = create_snapshot_checkpoint(str(ws.cwd), snap_dir, thread_id, msg)
            if result["status"] != "ok":
                logger.warning("_record_turn_checkpoint: snapshot falhou: %s", result)
                return
            strategy = "snapshot"
            snapshot_path = result["snapshot_path"]
            files_touched = __import__("json").dumps(result.get("files_touched", []))
            # GC: limpa snapshots antigos desta thread.
            gc_snapshots(snap_dir)

        now = datetime.now(UTC).isoformat()
        from backend.api.handlers.threads import _get_db

        db = await _get_db()
        await db.execute(
            "INSERT INTO vectora_checkpoint_artifacts "
            "(id, thread_id, checkpoint_id, strategy, git_sha, snapshot_path, files_touched, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                thread_id,
                checkpoint_id,
                strategy,
                git_sha,
                snapshot_path,
                files_touched,
                now,
            ),
        )
        await db.commit()
        logger.debug(
            "_record_turn_checkpoint: checkpoint gravado thread=%s strategy=%s",
            thread_id,
            strategy,
        )
    except Exception:
        logger.exception("_record_turn_checkpoint: falha ao gravar checkpoint de turno")


async def _mark_thread_has_content(thread_id: str) -> None:
    """Incrementa ``message_count`` quando o assistente emite o 1º token real.

    A visibilidade da thread em ListThreads/cleanup_empty_threads já é
    garantida antes disso — `stream_chat` (`api/handlers/chat.py`) incrementa
    assim que a mensagem do USUÁRIO é enviada, já que é o usuário quem inicia
    a conversa. Este incremento extra no lado do assistente só reflete que o
    turno teve resposta de verdade, sem ser o gate de visibilidade. Chamada
    fire-and-forget (``asyncio.ensure_future``, nunca ``await``ada) a partir
    de ``stream_engine_events`` no 1º token emitido — captura a própria
    exceção pra nunca gerar "Task exception was never retrieved".
    """
    try:
        from backend.api.handlers.threads import _increment_message_count

        await _increment_message_count(thread_id)
    except Exception:
        logger.warning(
            "stream_engine_events: falha ao marcar thread com conteúdo real",
            exc_info=True,
        )


async def _pre_approved(tool_name: str, args: dict, workspace_id: str) -> bool:
    """Anotação da aprovação inteligente pro `HITLEvent` — nunca decide
    sozinha, só marca a sugestão como reconhecida. Falha aqui nunca derruba
    o stream: é a mesma degradação de `evaluate_command`."""
    try:
        from backend.services.smart_approval import evaluate_command

        return await evaluate_command(tool_name, args, workspace_id=workspace_id)
    except Exception:
        logger.debug("adapters: smart_approval indisponível", exc_info=True)
        return False
