"""Modelo de Kanban sobre ``vectora_background_tasks``.

Extensão do schema existente, não um banco paralelo — o Vectora já tem
``BackgroundTask`` com ``kind="subagent"`` e worktree isolado
(``backend/scheduling/delegate.py``).

Três mecanismos vêm do Hermes (``hermes_cli/kanban_db.py``):

- **Claim atômico por CAS**: ``UPDATE ... WHERE status='ready' AND claim_lock
  IS NULL``. Ler-depois-escrever deixaria dois workers pegarem o mesmo card.
- **TTL no claim**: worker que morre sem liberar tem o card devolvido por
  expiração, em vez de deixá-lo preso em ``running`` para sempre.
- **Bloqueio tipado**: ``dependency`` fica em ``todo`` (não há ação humana
  possível, some do radar até a promoção automática); os demais vão pra
  ``blocked``, onde alguém precisa agir.

Invariante de produto: delegação síncrona (``task()`` no meio da conversa)
**não** participa do board — só background tasks. Isso já é o comportamento
do Vectora e é o mesmo desacoplamento do Hermes.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: Ciclo completo. `triage` e `archived` ficam fora das colunas principais
#: da UI, mas existem no modelo.
KANBAN_STATUSES: tuple[str, ...] = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
    "archived",
)

#: `dependency` é resolvido pela máquina; os outros esperam uma pessoa.
BLOCK_KINDS: tuple[str, ...] = (
    "dependency",
    "needs_input",
    "capability",
    "transient",
)

_DEFAULT_CLAIM_TTL_S = 900

#: Transições acionáveis por ação humana direta (drag-and-drop no board ou
#: `PATCH .../tasks/{id}` com campo `status`). `running` só é alcançado pelo
#: claim atômico do scheduler (`claim_task`) e `done` só pela run terminando
#: de verdade — nenhum dos dois aparece como alvo aqui, então nenhuma ação
#: manual chega lá por mais que o alvo esteja em `KANBAN_STATUSES`.
MANUAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "todo": frozenset({"ready", "triage"}),
    "ready": frozenset({"triage"}),
    "blocked": frozenset({"ready"}),
    #: Reprovar review — devolve pro fluxo ativo. Aprovar (`review` →
    #: `done`) NÃO entra aqui de propósito: é um endpoint dedicado
    #: (`POST /tasks/{id}/review/approve`), pra não abrir um `*→done`
    #: genérico pro drag-and-drop e pra registrar quem aprovou.
    "review": frozenset({"ready"}),
    #: Reabertura manual de uma task já concluída — único caminho de volta
    #: pra `done`, nunca automático.
    "done": frozenset({"review"}),
}

#: Bloqueios consecutivos (não-`dependency`) antes de escalar pra `triage`
#: em vez de deixar o card preso em `blocked` pra sempre esperando alguém
#: notar. Zera quando a task sai de `blocked` com sucesso (`set_status`
#: pra `ready`/`done`, ou `unblock_task` explícito).
BLOCK_RECURRENCE_LIMIT = 3


async def _get_db() -> Any:
    """Mesmo banco de `vectora_background_tasks` — não `checkpoints.db`.

    `vectora_background_tasks`/`vectora_task_links` vivem em
    `settings.db_dsn` (aplicado por `schema.sql`), não no banco de
    threads/checkpoints usado por `threads.py::_get_db`. Usar o banco
    errado aqui faz todo claim/status do Kanban cair num banco sem essas
    tabelas — o try/except do `tick()` silencia o erro, então o sintoma
    vira só "nada do Kanban nunca atualiza".
    """
    from backend.scheduling.background_tasks import _get_db as _tasks_db

    return await _tasks_db()


def _agora() -> datetime:
    return datetime.now(UTC)


async def _emit_kanban_event(
    task_id: str,
    status: str,
    *,
    block_kind: str | None = None,
    block_reason: str | None = None,
) -> None:
    """Notifica o board (SSE) sobre a mudança de status do card.

    Reaproveita o canal genérico de webhooks (`_emit_sse_event`) em vez de
    um endpoint dedicado — o frontend já assina `/webhook/events`. Falha
    aqui é só a camada de notificação em tempo real: a transação de status
    já foi commitada por quem chamou, então um erro de emissão não pode
    propagar e desfazer o que já aconteceu no banco.
    """
    try:
        from backend.api.handlers.webhooks import _emit_sse_event

        # `board_id` não chega por parâmetro dos callers (set_status,
        # unblock_task, block_task) — uma query pequena aqui, não um
        # replay de assinatura em cada call site. Sem isso, um board
        # reconciliando por SSE não sabe se o evento é dele (cairia
        # sempre no fallback de refetch completo).
        board_id = None
        try:
            db = await _get_db()
            async with db.execute(
                "SELECT board_id FROM vectora_background_tasks WHERE id = ?",
                (task_id,),
            ) as cur:
                row = await cur.fetchone()
            board_id = row["board_id"] if row else None
        except Exception:
            logger.debug(
                "kanban: falha ao buscar board_id pro evento SSE", exc_info=True
            )

        _emit_sse_event(
            provider="kanban",
            event_type="kanban_task.status_changed",
            data={
                "task_id": task_id,
                "board_id": board_id,
                "status": status,
                "block_kind": block_kind,
                "block_reason": block_reason,
            },
        )
    except Exception:
        logger.debug("kanban: falha ao emitir evento SSE de status", exc_info=True)


async def _record_task_event(
    task_id: str,
    from_status: str | None,
    to_status: str,
    *,
    block_kind: str | None = None,
    block_reason: str | None = None,
) -> None:
    """Grava a transição em `vectora_task_events` — a timeline consultável
    que `_emit_kanban_event` (SSE efêmero) nunca persiste.

    Mesmo contrato de falha do `_emit_kanban_event`: a transição de status já
    foi commitada por quem chamou, então um erro aqui é só perda de uma linha
    de histórico, não pode desfazer a transição real.
    """
    try:
        db = await _get_db()
        await db.execute(
            "INSERT INTO vectora_task_events "
            "(id, task_id, from_status, to_status, block_kind, block_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                from_status,
                to_status,
                block_kind,
                block_reason,
            ),
        )
        await db.commit()
    except Exception:
        logger.debug("kanban: falha ao gravar evento de timeline", exc_info=True)


async def get_task_status(task_id: str) -> dict[str, Any]:
    db = await _get_db()
    async with db.execute(
        "SELECT status, block_kind, block_reason, claim_lock, claim_expires_at "
        "FROM vectora_background_tasks WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        msg = f"task {task_id!r} não existe"
        raise ValueError(msg)
    return dict(row)


async def authorize_task_session(task_id: str, session_id: str) -> None:
    """Authorize a task mutation for the session that owns the task.

    Raises ``ValueError`` when the task does not exist or its persisted
    session differs from ``session_id``.
    """
    db = await _get_db()
    async with db.execute(
        "SELECT session_id FROM vectora_background_tasks WHERE id = ?", (task_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"task {task_id!r} não existe")
    if row["session_id"] != session_id:
        raise ValueError("task não pertence à sessão atual")


async def set_status(
    task_id: str, status: str, *, authorized_run_id: str | None = None
) -> None:
    """Atualiza o status do card, opcionalmente sob fencing de uma run.

    Quando ``authorized_run_id`` é informado, a escrita só ocorre enquanto o
    claim da run continua vigente; uma perda do claim levanta ``ValueError``.
    """
    if status not in KANBAN_STATUSES:
        msg = (
            f"status {status!r} fora da taxonomia — válidos: "
            f"{', '.join(KANBAN_STATUSES)}"
        )
        raise ValueError(msg)
    db = await _get_db()
    async with db.execute(
        "SELECT status, trigger_type, trigger_config "
        "FROM vectora_background_tasks WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    from_status = row["status"] if row else None
    next_run_at: str | None = None
    interval_config_raw: object | None = None
    is_interval_completion = (
        authorized_run_id is not None
        and status in ("ready", "done")
        and row is not None
        and row["trigger_type"] == "interval"
    )
    if is_interval_completion:
        # Advance the occurrence in the same transaction that releases its
        # claim; otherwise another scheduler can reclaim the old due time.
        from backend.scheduling.background_tasks import _next_run

        interval_config_raw = row["trigger_config"]

        def _next_interval_run(raw: object) -> str | None:
            config_value: object = raw
            if isinstance(raw, str):
                try:
                    config_value = json.loads(raw)
                except json.JSONDecodeError:
                    config_value = {}
            config = config_value if isinstance(config_value, dict) else {}
            cron_expr = config.get("cron_expr")
            return _next_run(cron_expr if isinstance(cron_expr, str) else None)

        next_run_at = _next_interval_run(interval_config_raw)
    if status in ("ready", "done"):
        # Saída bem-sucedida do ciclo de bloqueio — zera o contador de
        # escalonamento (ver BLOCK_RECURRENCE_LIMIT), senão uma task que
        # falhou 2x e depois teve sucesso carregaria o histórico pra
        # sempre em vez de resetar a régua.
        if authorized_run_id is None:
            cur = await db.execute(
                "UPDATE vectora_background_tasks SET status = ?, block_count = 0, "
                "updated_at = datetime('now') WHERE id = ?",
                (status, task_id),
            )
        elif is_interval_completion:
            # A concurrent schedule edit may replace trigger_config after
            # the snapshot above. Retry from the fresh config rather than
            # overwriting its next_run_at with the old cron expression.
            while True:
                cur = await db.execute(
                    "UPDATE vectora_background_tasks SET status = ?, "
                    "block_count = 0, next_run_at = ?, claim_lock = NULL, "
                    "claim_expires_at = NULL, updated_at = datetime('now') "
                    "WHERE id = ? AND claim_lock = ? AND claim_expires_at > ? "
                    "AND trigger_config = ?",
                    (
                        status,
                        next_run_at,
                        task_id,
                        authorized_run_id,
                        _agora().isoformat(),
                        interval_config_raw,
                    ),
                )
                if cur.rowcount:
                    break
                async with db.execute(
                    "SELECT trigger_config, claim_lock, claim_expires_at "
                    "FROM vectora_background_tasks WHERE id = ?",
                    (task_id,),
                ) as latest_cur:
                    latest = await latest_cur.fetchone()
                now_iso = _agora().isoformat()
                if (
                    latest is None
                    or latest["claim_lock"] != authorized_run_id
                    or not latest["claim_expires_at"]
                    or latest["claim_expires_at"] <= now_iso
                ):
                    await db.rollback()
                    raise ValueError("execução perdeu o claim da task")
                interval_config_raw = latest["trigger_config"]
                next_run_at = _next_interval_run(interval_config_raw)
        else:
            cur = await db.execute(
                "UPDATE vectora_background_tasks SET status = ?, "
                "block_count = 0, claim_lock = NULL, claim_expires_at = NULL, "
                "updated_at = datetime('now') WHERE id = ? AND claim_lock = ? "
                "AND claim_expires_at > ?",
                (status, task_id, authorized_run_id, _agora().isoformat()),
            )
    elif authorized_run_id is None:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (status, task_id),
        )
    else:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = ?, "
            "claim_lock = NULL, claim_expires_at = NULL, "
            "updated_at = datetime('now') WHERE id = ? AND claim_lock = ? "
            "AND claim_expires_at > ?",
            (status, task_id, authorized_run_id, _agora().isoformat()),
        )
    if authorized_run_id is not None and cur.rowcount == 0:
        await db.rollback()
        raise ValueError("execução perdeu o claim da task")
    await db.commit()
    await _emit_kanban_event(task_id, status)
    await _record_task_event(task_id, from_status, status)


async def manual_transition(
    task_id: str,
    target_status: str,
    *,
    authorized_session_id: str | None = None,
    authorized_run_id: str | None = None,
) -> None:
    """Move a task por ação humana direta (drag-and-drop ou `PATCH` de status).

    Valida a transição contra `MANUAL_TRANSITIONS`, não só o alvo contra
    `KANBAN_STATUSES` — `set_status` sozinho aceitaria `*→running`/`*→done`,
    que precisam ficar exclusivos do claim atômico e da run terminando.
    `blocked→ready` passa por `unblock_task` (mesma função do botão
    "Desbloquear") para também limpar `block_kind`/`block_reason`.
    """
    estado = await get_task_status(task_id)
    if authorized_session_id is not None:
        db = await _get_db()
        async with db.execute(
            "SELECT session_id FROM vectora_background_tasks WHERE id = ?",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["session_id"] != authorized_session_id:
            raise ValueError("task não pertence à sessão atual")
    atual = estado["status"]
    permitidos = MANUAL_TRANSITIONS.get(atual, frozenset())
    if target_status not in permitidos:
        msg = f"transição {atual!r} → {target_status!r} não é permitida manualmente"
        raise ValueError(msg)
    if atual == "blocked" and target_status == "ready":
        await unblock_task(task_id, authorized_run_id=authorized_run_id)
        return
    await set_status(task_id, target_status, authorized_run_id=authorized_run_id)


async def claim_task(
    task_id: str, run_id: str, *, ttl_s: int = _DEFAULT_CLAIM_TTL_S
) -> bool:
    """Reivindica a task para `run_id`. `False` quando outro já pegou.

    O CAS está no `WHERE`: só troca de dono se ainda estiver `ready`/
    `scheduled` e sem claim. `scheduled` entra aqui porque toda task
    recorrente/agendada nasce nesse status e só o tick do scheduler decide
    *quando* ela é due — não é o status quem barra isso,
    é o filtro por `next_run_at` em `_list_due_interval_tasks`. Checar
    antes e gravar depois abriria a janela em que dois workers leem
    "livre" e ambos gravam.
    """
    expira = (_agora() + timedelta(seconds=ttl_s)).isoformat()
    db = await _get_db()
    cur = await db.execute(
        """
        UPDATE vectora_background_tasks
           SET status = 'running',
               claim_lock = ?,
               claim_expires_at = ?,
               updated_at = datetime('now')
         WHERE id = ?
           AND status IN ('ready', 'scheduled')
           AND claim_lock IS NULL
        """,
        (run_id, expira, task_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def ensure_task_claim(
    task_id: str, run_id: str, *, ttl_s: int = _DEFAULT_CLAIM_TTL_S
) -> bool:
    """Mantém ou recupera o claim da própria run sem roubar outro worker.

    Uma run pausada para aprovação pode sobreviver ao TTL do claim. Ela pode
    reassumir o card somente enquanto o lock ainda é dela ou depois que o
    lock foi liberado; um claim pertencente a outra run sempre é recusado.
    """
    if not run_id:
        return False
    expira = (_agora() + timedelta(seconds=ttl_s)).isoformat()
    db = await _get_db()
    async with db.execute(
        "SELECT claim_lock, claim_expires_at FROM vectora_background_tasks "
        "WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return False
    cur = await db.execute(
        "UPDATE vectora_background_tasks SET status = 'running', claim_lock = ?, "
        "claim_expires_at = ?, updated_at = datetime('now') WHERE id = ? AND "
        "((claim_lock = ?) OR "
        "(claim_lock IS NULL AND status IN ('ready', 'scheduled')))",
        (run_id, expira, task_id, run_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def authorize_task_claim(task_id: str, run_id: str) -> None:
    """Confirma que uma execução ainda detém o claim do card.

    Levanta ``ValueError`` para IDs ausentes, claims pertencentes a outra
    execução ou claims expirados; isso impede que uma run zumbi altere um
    card já reivindicado por outro worker.
    """
    if not run_id:
        raise ValueError("run_id ausente no contexto da execução")
    db = await _get_db()
    async with db.execute(
        "SELECT claim_lock, claim_expires_at FROM vectora_background_tasks "
        "WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError("task não encontrada")
    if row["claim_lock"] != run_id:
        raise ValueError("execução não possui o claim atual da task")
    try:
        expirado = datetime.fromisoformat(row["claim_expires_at"]) <= _agora()
    except (TypeError, ValueError):
        expirado = True
    if expirado:
        raise ValueError("claim da execução expirou")


async def heartbeat_claim(
    task_id: str, run_id: str, *, ttl_s: int = _DEFAULT_CLAIM_TTL_S
) -> bool:
    """Estende o TTL do claim — não é um "estou vivo" simbólico.

    Só o dono do claim consegue estender: sem essa checagem, um worker
    zumbi manteria vivo o claim de outro.
    """
    expira = (_agora() + timedelta(seconds=ttl_s)).isoformat()
    db = await _get_db()
    cur = await db.execute(
        "UPDATE vectora_background_tasks SET claim_expires_at = ? "
        "WHERE id = ? AND claim_lock = ?",
        (expira, task_id, run_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def release_stale_claims() -> int:
    """Devolve pra `ready` os claims expirados. Roda no tick do scheduler."""
    agora = _agora().isoformat()
    db = await _get_db()
    cur = await db.execute(
        """
        UPDATE vectora_background_tasks
           SET status = 'ready',
               claim_lock = NULL,
               claim_expires_at = NULL,
               updated_at = datetime('now')
         WHERE claim_lock IS NOT NULL
           AND claim_expires_at IS NOT NULL
           AND claim_expires_at < ?
        """,
        (agora,),
    )
    await db.commit()
    if cur.rowcount:
        logger.info("kanban: %s claim(s) expirado(s) devolvido(s)", cur.rowcount)
    return cur.rowcount


async def block_task(
    task_id: str,
    kind: str,
    reason: str,
    *,
    authorized_run_id: str | None = None,
) -> None:
    """Bloqueia a task. `dependency` fica em `todo`; o resto vai pra `blocked`.

    Bloqueios não-`dependency` incrementam `block_count`; ao atingir
    `BLOCK_RECURRENCE_LIMIT`, escala pra `triage` em vez de `blocked` — um
    card que falha sempre do mesmo jeito precisa de decisão humana, não de
    ficar invisível na coluna esperando alguém notar.
    """
    if kind not in BLOCK_KINDS:
        msg = (
            f"tipo de bloqueio {kind!r} fora da taxonomia — válidos: "
            f"{', '.join(BLOCK_KINDS)}"
        )
        raise ValueError(msg)

    db = await _get_db()

    if kind == "dependency":
        async with db.execute(
            "SELECT status FROM vectora_background_tasks WHERE id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        from_status = row["status"] if row else None
        # Bloqueio por dependência não é acionável por ninguém: colocá-lo em
        # `blocked` encheria a coluna de cards que a pessoa não pode destravar.
        if authorized_run_id is None:
            cur = await db.execute(
                "UPDATE vectora_background_tasks SET status = 'todo', block_kind = ?, "
                "block_reason = ?, claim_lock = NULL, claim_expires_at = NULL, "
                "updated_at = datetime('now') WHERE id = ?",
                (kind, reason, task_id),
            )
        else:
            cur = await db.execute(
                "UPDATE vectora_background_tasks SET status = 'todo', block_kind = ?, "
                "block_reason = ?, claim_lock = NULL, claim_expires_at = NULL, "
                "updated_at = datetime('now') WHERE id = ? AND claim_lock = ? "
                "AND claim_expires_at > ?",
                (kind, reason, task_id, authorized_run_id, _agora().isoformat()),
            )
        if authorized_run_id is not None and cur.rowcount == 0:
            await db.rollback()
            raise ValueError("execução perdeu o claim da task")
        await db.commit()
        await _emit_kanban_event(task_id, "todo", block_kind=kind, block_reason=reason)
        await _record_task_event(
            task_id, from_status, "todo", block_kind=kind, block_reason=reason
        )
        return

    async with db.execute(
        "SELECT status, block_count FROM vectora_background_tasks WHERE id = ?",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    from_status = row["status"] if row else None
    novo_count = ((row["block_count"] if row else 0) or 0) + 1

    if novo_count >= BLOCK_RECURRENCE_LIMIT:
        status = "triage"
        reason = (
            f"{reason} — bloqueada {novo_count}x seguidas, revisão manual necessária"
        )
    else:
        status = "blocked"

    if authorized_run_id is None:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = ?, block_kind = ?, "
            "block_reason = ?, block_count = ?, claim_lock = NULL, "
            "claim_expires_at = NULL, updated_at = datetime('now') WHERE id = ?",
            (status, kind, reason, novo_count, task_id),
        )
    else:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = ?, block_kind = ?, "
            "block_reason = ?, block_count = ?, claim_lock = NULL, "
            "claim_expires_at = NULL, updated_at = datetime('now') WHERE id = ? "
            "AND claim_lock = ? AND claim_expires_at > ?",
            (
                status,
                kind,
                reason,
                novo_count,
                task_id,
                authorized_run_id,
                _agora().isoformat(),
            ),
        )
    if authorized_run_id is not None and cur.rowcount == 0:
        await db.rollback()
        raise ValueError("execução perdeu o claim da task")
    await db.commit()
    await _emit_kanban_event(task_id, status, block_kind=kind, block_reason=reason)
    await _record_task_event(
        task_id, from_status, status, block_kind=kind, block_reason=reason
    )


async def unblock_task(task_id: str, *, authorized_run_id: str | None = None) -> None:
    """Desbloqueia um card e limpa seus metadados de bloqueio.

    Com ``authorized_run_id``, a alteração exige um claim vigente da mesma
    run e levanta ``ValueError`` se o fencing rejeitar a escrita.
    """
    db = await _get_db()
    async with db.execute(
        "SELECT status FROM vectora_background_tasks WHERE id = ?", (task_id,)
    ) as cur:
        row = await cur.fetchone()
    from_status = row["status"] if row else None
    if authorized_run_id is None:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = 'ready', block_kind = NULL, "
            "block_reason = NULL, block_count = 0, updated_at = datetime('now') "
            "WHERE id = ?",
            (task_id,),
        )
    else:
        cur = await db.execute(
            "UPDATE vectora_background_tasks SET status = 'ready', block_kind = NULL, "
            "block_reason = NULL, block_count = 0, updated_at = datetime('now') "
            "WHERE id = ? AND claim_lock = ? AND claim_expires_at > ?",
            (task_id, authorized_run_id, _agora().isoformat()),
        )
    if authorized_run_id is not None and cur.rowcount == 0:
        await db.rollback()
        raise ValueError("execução perdeu o claim da task")
    await db.commit()
    await _emit_kanban_event(task_id, "ready", block_kind=None, block_reason=None)
    await _record_task_event(task_id, from_status, "ready")


async def get_dependencies(task_id: str) -> list[dict[str, Any]]:
    """Pais diretos de `task_id` (não transitivos) com status atual —
    fonte real do contador N/M e da lista `blocked_by` no card do Kanban,
    substituindo o campo que hoje o frontend declara mas o backend nunca
    populava."""
    db = await _get_db()
    async with db.execute(
        """
        SELECT t.id, t.name, t.status
        FROM vectora_task_links l
        JOIN vectora_background_tasks t ON t.id = l.parent_id
        WHERE l.child_id = ?
        ORDER BY t.created_at
        """,
        (task_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r["id"], "name": r["name"], "status": r["status"]} for r in rows]


async def get_progress(task_id: str) -> dict[str, int] | None:
    """Rollup de subtasks diretas (`task_id` como pai) — `{done, total}`.

    `None` (não `{"done": 0, "total": 0}`) quando a task não tem subtask
    nenhuma: o card do Kanban desenha a barra de progresso só quando há
    algo pra medir — `{0, 0}` renderizaria uma barra vazia sem sentido
    pra toda task folha (a maioria delas).
    """
    db = await _get_db()
    async with db.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE t.status IN ('done', 'archived')) AS done
        FROM vectora_task_links l
        JOIN vectora_background_tasks t ON t.id = l.child_id
        WHERE l.parent_id = ?
        """,
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
    total = row["total"] if row else 0
    if not total:
        return None
    return {"done": row["done"], "total": total}


async def get_progress_batch(task_ids: list[str]) -> dict[str, dict[str, int]]:
    """Mesmo rollup de `get_progress`, em lote — usado pelo board agregado
    (`GET .../board`) pra não virar uma query por card. Chaves ausentes no
    dict de retorno = sem subtask (mesmo `None` implícito de `get_progress`).
    """
    if not task_ids:
        return {}
    db = await _get_db()
    placeholders = ",".join("?" for _ in task_ids)
    query = f"SELECT l.parent_id AS task_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE t.status IN ('done', 'archived')) AS done FROM vectora_task_links l JOIN vectora_background_tasks t ON t.id = l.child_id WHERE l.parent_id IN ({placeholders}) GROUP BY l.parent_id"  # noqa: S608  # nosec B608
    async with db.execute(query, task_ids) as cur:
        rows = await cur.fetchall()
    return {r["task_id"]: {"done": r["done"], "total": r["total"]} for r in rows}


async def get_comment_counts_batch(task_ids: list[str]) -> dict[str, int]:
    """Contagem de comentários por task, em lote — mesma razão de existir
    de `get_progress_batch`."""
    if not task_ids:
        return {}
    db = await _get_db()
    placeholders = ",".join("?" for _ in task_ids)
    query = f"SELECT task_id, COUNT(*) AS n FROM vectora_task_comments WHERE task_id IN ({placeholders}) GROUP BY task_id"  # noqa: S608  # nosec B608
    async with db.execute(query, task_ids) as cur:
        rows = await cur.fetchall()
    return {r["task_id"]: r["n"] for r in rows}


async def get_dependencies_batch(
    task_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Mesma lista de `get_dependencies` (pais diretos), em lote."""
    if not task_ids:
        return {}
    db = await _get_db()
    placeholders = ",".join("?" for _ in task_ids)
    query = f"SELECT l.child_id AS task_id, t.id, t.name, t.status FROM vectora_task_links l JOIN vectora_background_tasks t ON t.id = l.parent_id WHERE l.child_id IN ({placeholders}) ORDER BY t.created_at"  # noqa: S608  # nosec B608
    async with db.execute(query, task_ids) as cur:
        rows = await cur.fetchall()
    out: dict[str, list[dict[str, Any]]] = {tid: [] for tid in task_ids}
    for r in rows:
        out[r["task_id"]].append(
            {"id": r["id"], "name": r["name"], "status": r["status"]}
        )
    return out


async def _depende_de(task_id: str) -> set[str]:
    """Ancestrais de `task_id` — usado pra detectar ciclo."""
    db = await _get_db()
    vistos: set[str] = set()
    fila = [task_id]
    while fila:
        atual = fila.pop()
        async with db.execute(
            "SELECT parent_id FROM vectora_task_links WHERE child_id = ?", (atual,)
        ) as cur:
            pais = [r["parent_id"] for r in await cur.fetchall()]
        for pai in pais:
            if pai not in vistos:
                vistos.add(pai)
                fila.append(pai)
    return vistos


async def add_dependency(parent_id: str, child_id: str) -> None:
    """`child_id` só fica pronto quando `parent_id` conclui."""
    if parent_id == child_id:
        msg = "dependência circular: uma task não pode depender de si mesma"
        raise ValueError(msg)

    # Ciclo trava os dois cards em `todo` para sempre, e o motivo não
    # aparece em lugar nenhum — recusar na criação é a única chance.
    if child_id in await _depende_de(parent_id):
        msg = (
            f"dependência circular: {parent_id!r} já depende (direta ou "
            f"indiretamente) de {child_id!r}"
        )
        raise ValueError(msg)

    db = await _get_db()
    await db.execute(
        "INSERT OR IGNORE INTO vectora_task_links (parent_id, child_id) VALUES (?, ?)",
        (parent_id, child_id),
    )
    await db.commit()


async def remove_dependency(parent_id: str, child_id: str) -> bool:
    """Remove o vínculo `parent_id → child_id`, se existir.

    `False` quando o vínculo não existia (nada a remover) — o caller (HTTP
    handler) decide se isso é 404 ou um no-op silencioso; aqui é só o fato.
    """
    db = await _get_db()
    cur = await db.execute(
        "DELETE FROM vectora_task_links WHERE parent_id = ? AND child_id = ?",
        (parent_id, child_id),
    )
    await db.commit()
    return cur.rowcount > 0


async def recompute_ready() -> int:
    """Promove pra `ready` as tasks em `todo` com todos os pais concluídos.

    Chamado quando uma task chega em `done`. Um filho com dois pais espera
    os dois — promover com um só o faria rodar sem o resultado do outro.
    """
    db = await _get_db()
    async with db.execute(
        """
        SELECT t.id
          FROM vectora_background_tasks t
         WHERE t.status = 'todo'
           AND EXISTS (
                 SELECT 1 FROM vectora_task_links l WHERE l.child_id = t.id
               )
           AND NOT EXISTS (
                 SELECT 1
                   FROM vectora_task_links l
                   JOIN vectora_background_tasks p ON p.id = l.parent_id
                  WHERE l.child_id = t.id
                    AND p.status != 'done'
               )
        """
    ) as cur:
        prontas = [r["id"] for r in await cur.fetchall()]

    for task_id in prontas:
        await db.execute(
            "UPDATE vectora_background_tasks SET status = 'ready', "
            "block_kind = NULL, block_reason = NULL, "
            "updated_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
    await db.commit()
    return len(prontas)


async def approve_review(task_id: str, user_id: str) -> None:
    """Aprova uma task em `review`, movendo pra `done`.

    Endpoint dedicado (não `manual_transition`/`MANUAL_TRANSITIONS`) de
    propósito: `review → done` nunca deveria abrir como transição genérica
    de drag-and-drop — precisa registrar QUEM aprovou, o que
    `manual_transition` não faz. `review → ready` (reprovar) segue pelo
    caminho genérico normalmente.
    """
    estado = await get_task_status(task_id)
    if estado["status"] != "review":
        msg = f"task está em {estado['status']!r}, não em 'review' — nada a aprovar"
        raise ValueError(msg)
    await set_status(task_id, "done")
    await add_comment(task_id, user_id, "✓ Review aprovada.")


async def add_comment(task_id: str, user_id: str, body: str) -> dict[str, Any]:
    """Grava um comentário no card. Corpo vazio/whitespace é recusado —
    não existe comentário sem conteúdo no board."""
    corpo = body.strip()
    if not corpo:
        msg = "comentário vazio não é permitido"
        raise ValueError(msg)

    comment_id = str(uuid.uuid4())
    db = await _get_db()
    await db.execute(
        "INSERT INTO vectora_task_comments (id, task_id, user_id, body) "
        "VALUES (?, ?, ?, ?)",
        (comment_id, task_id, user_id, corpo),
    )
    await db.commit()
    async with db.execute(
        "SELECT id, task_id, user_id, body, created_at "
        "FROM vectora_task_comments WHERE id = ?",
        (comment_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row)


async def list_comments(task_id: str) -> list[dict[str, Any]]:
    """Comentários do card em ordem cronológica."""
    db = await _get_db()
    async with db.execute(
        "SELECT id, task_id, user_id, body, created_at "
        "FROM vectora_task_comments WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_events(task_id: str) -> list[dict[str, Any]]:
    """Timeline de transições de status do card, em ordem cronológica —
    gravada por `_record_task_event` a cada `set_status`/`block_task`/
    `unblock_task`."""
    db = await _get_db()
    async with db.execute(
        "SELECT id, task_id, from_status, to_status, block_kind, block_reason, "
        "created_at FROM vectora_task_events WHERE task_id = ? "
        "ORDER BY created_at ASC",
        (task_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
