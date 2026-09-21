"""Tools de agente para o board Kanban (``vectora_background_tasks``).

Expõe ao agente, durante a conversa, o que hoje só existe como agendamento
(`create_background_task`/`schedule_task` em `backend/tools/background.py`):
ler e mover cards no mesmo board que a sidebar de Tarefas mostra.

Máquina de estados real em `backend.scheduling.kanban` — estas tools nunca
fazem `UPDATE` arbitrário de `status`; delegam a `set_status`/`block_task`/
`unblock_task`, que validam a transição e recusam o que for inválido.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.scheduling import background_tasks, kanban
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

#: Bloqueio padrão quando `kanban_update_status(status="blocked")` não
#: especifica `block_kind` — o motivo mais comum de um agente se bloquear
#: sozinho é faltar algo que só uma pessoa resolve.
_DEFAULT_BLOCK_KIND = "needs_input"

_DECOMPOSE_SYSTEM_PROMPT = (
    "You split a task card into a dependency graph of smaller subtasks. "
    "Respond with ONLY a JSON array, no prose, no markdown fences. Each "
    'element: {"name": "short title", "instruction": "complete standalone '
    'instruction for an agent to execute", "parents": [indexes]}. '
    '"parents" lists the 0-based indexes (within this same array) of '
    "subtasks that must complete before this one can start — empty list if "
    "none. If the task is already atomic and doesn't benefit from "
    "splitting, respond with an empty JSON array []."
)


def _parse_decomposition(raw_text: str) -> list[dict[str, Any]]:
    """Faz o parse tolerante da proposta do modelo — nunca levanta exceção.

    Formato malformado (JSON inválido, não é uma lista, item sem `name`/
    `instruction`, `parents` não é lista de int) faz o nó ser descartado
    silenciosamente em vez de derrubar a decomposição inteira; JSON
    completamente inválido devolve lista vazia (card fica em `triage`)."""
    texto = raw_text.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        primeira_linha_fim = texto.find("\n")
        if primeira_linha_fim != -1 and texto[:primeira_linha_fim].strip().isalpha():
            texto = texto[primeira_linha_fim + 1 :]
    try:
        bruto = json.loads(texto)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(bruto, list):
        return []

    nos: list[dict[str, Any]] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        nome = item.get("name")
        instrucao = item.get("instruction")
        if not isinstance(nome, str) or not nome.strip():
            continue
        if not isinstance(instrucao, str) or not instrucao.strip():
            continue
        parents_raw = item.get("parents", [])
        parents = (
            [p for p in parents_raw if isinstance(p, int)]
            if isinstance(parents_raw, list)
            else []
        )
        nos.append(
            {"name": nome.strip(), "instruction": instrucao.strip(), "parents": parents}
        )
    return nos


@vtool(extras=ToolExtras(destructive=False, category="workspace", icon="trello"))
async def kanban_list(
    ctx: ToolContext,
    status: str | None = None,
    agent_profile_id: str | None = None,
) -> str:
    """Lista os cards do board Kanban desta sessão, com filtro opcional.

    Só leitura — nunca pede aprovação (HITL).

    Args:
        status: filtra por coluna (`triage`|`todo`|`scheduled`|`ready`|
            `running`|`blocked`|`review`|`done`|`archived`). Sem filtro,
            lista todas.
        agent_profile_id: filtra pelos cards que herdam este perfil de
            agente. Sem filtro, lista independente do perfil.

    Returns:
        JSON com `cards`: id, name, kind, status, block_kind, block_reason,
        agent_profile_id, last_run_at.
    """
    try:
        if status is not None and status not in kanban.KANBAN_STATUSES:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        f"status {status!r} fora da taxonomia — válidos: "
                        f"{', '.join(kanban.KANBAN_STATUSES)}"
                    ),
                }
            )

        session_id = ctx.thread_id
        if not session_id:
            return json.dumps(
                {"status": "error", "error": "session_id ausente no contexto"}
            )

        tasks = await background_tasks.list_tasks(session_id)
        cards = [
            {
                "task_id": t.id,
                "name": t.name,
                "kind": t.kind,
                "status": t.status,
                "block_kind": t.block_kind,
                "block_reason": t.block_reason,
                "agent_profile_id": t.agent_profile_id,
                "last_run_at": t.last_run_at,
            }
            for t in tasks
            if (status is None or t.status == status)
            and (agent_profile_id is None or t.agent_profile_id == agent_profile_id)
        ]
        return json.dumps({"status": "ok", "cards": cards}, ensure_ascii=False)
    except Exception as e:
        logger.exception(
            "kanban_list: erro inesperado", extra={"status_filtro": status}
        )
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=True,
        category="workspace",
        icon="plus-square",
    )
)
async def kanban_create(
    ctx: ToolContext,
    name: str,
    instruction: str,
    kind: str = "subagent",
    agent_profile_id: str | None = None,
) -> str:
    """Cria um card novo no board Kanban, pronto pra rodar sob demanda.

    Diferente de `create_background_task`/`schedule_task` (que agendam
    execução autônoma via cron/webhook), o card nasce com
    `trigger_type="manual"` — só dispara quando algo (o usuário, outra task,
    `run_background_task_now`) pedir explicitamente.

    Args:
        name: nome curto do card (coluna do board).
        instruction: instrução completa que o agente executará ao rodar.
        kind: `subagent` (default) | `routine` | `heartbreak`.
        agent_profile_id: perfil de agente a herdar (instrução/modelo/
            budget), se algum.

    Returns:
        JSON com `task_id` e `status` (coluna inicial do card) em sucesso.
    """
    try:
        if not name.strip():
            return json.dumps({"status": "error", "error": "name não pode ser vazio"})
        if not instruction.strip():
            return json.dumps(
                {"status": "error", "error": "instruction não pode ser vazia"}
            )

        session_id = ctx.thread_id
        user_id = ctx.user_id
        workspace_id = ctx.workspace_id or None
        if not session_id:
            return json.dumps(
                {"status": "error", "error": "session_id ausente no contexto"}
            )

        task = await background_tasks.create_task(
            session_id=session_id,
            user_id=user_id,
            kind=kind,
            name=name,
            instruction=instruction,
            trigger_type="manual",
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
        )
        return json.dumps(
            {"status": "created", "task_id": task.id, "kanban_status": task.status}
        )
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})
    except Exception as e:
        logger.exception("kanban_create: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=True,
        category="workspace",
        icon="move",
    )
)
async def kanban_update_status(
    ctx: ToolContext,
    task_id: str,
    status: str,
    block_kind: str | None = None,
    block_reason: str | None = None,
) -> str:
    """Move um card do board pra outra coluna, pela máquina de estados real.

    Não é um `UPDATE` livre: `status="blocked"` passa por `block_task`
    (tipa o bloqueio, decide `todo` vs `blocked` vs escalonamento pra
    `triage`); `status="ready"` passa por `unblock_task` (limpa o motivo do
    bloqueio); qualquer outra coluna passa por `set_status`, que recusa
    valores fora de `KANBAN_STATUSES`.

    Args:
        task_id: id do card (de `kanban_list`).
        status: coluna alvo (`triage`|`todo`|`scheduled`|`ready`|`running`|
            `blocked`|`review`|`done`|`archived`).
        block_kind: só usado quando `status="blocked"` — `dependency`|
            `needs_input`|`capability`|`transient`. Default `needs_input`.
        block_reason: só usado quando `status="blocked"` — motivo legível.

    Returns:
        JSON com o novo estado (`status`, `block_kind`, `block_reason`) em
        sucesso, ou erro tipado se a task não existe ou a transição é
        inválida.
    """
    try:
        if ctx.background_task_id:
            if ctx.background_task_id != task_id:
                return json.dumps(
                    {
                        "status": "error",
                        "error": "task em execução não corresponde ao card solicitado",
                    }
                )
            await kanban.authorize_task_claim(task_id, ctx.background_run_id)
            authorized_session_id: str | None = None
        else:
            if not ctx.thread_id:
                return json.dumps(
                    {"status": "error", "error": "session_id ausente no contexto"}
                )
            await kanban.authorize_task_session(task_id, ctx.thread_id)
            authorized_session_id = ctx.thread_id
        if status in ("running", "done"):
            return json.dumps(
                {
                    "status": "error",
                    "error": "running/done só podem ser alcançados pelos fluxos de execução",
                }
            )
        if status == "blocked":
            await kanban.block_task(
                task_id, block_kind or _DEFAULT_BLOCK_KIND, block_reason or ""
            )
        elif status == "ready":
            await kanban.manual_transition(
                task_id, status, authorized_session_id=authorized_session_id
            )
        else:
            await kanban.manual_transition(
                task_id, status, authorized_session_id=authorized_session_id
            )

        estado: dict[str, Any] = await kanban.get_task_status(task_id)
        return json.dumps({"result": "ok", "task_id": task_id, **estado})
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})
    except Exception as e:
        logger.exception(
            "kanban_update_status: erro inesperado",
            extra={"task_id": task_id, "status_pedido": status},
        )
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=True,
        category="workspace",
        icon="git-branch",
    )
)
async def kanban_decompose(
    ctx: ToolContext,
    task_id: str,
) -> str:
    """Decompõe um card em `triage` num grafo de subtasks com dependências.

    Chama um modelo auxiliar pra propor children (nome, instrução completa
    e dependências entre eles), cria cada um via a mesma `create_task`
    usada por `kanban_create`, liga as dependências propostas via
    `add_dependency` (ciclo proposto pelo modelo é rejeitado nó a nó, sem
    derrubar o resto da decomposição), e arquiva o card original — o
    trabalho dele agora vive nos children.

    Fallback determinístico: se o modelo não devolver JSON válido, ou
    devolver uma lista vazia (task já é atômica), o card original
    **não muda** — continua em `triage` esperando ação manual, nunca quebra
    o pipeline.

    Args:
        task_id: id do card em `triage` a decompor.

    Returns:
        JSON com `decomposed` (bool) e, se `True`, `children` (lista de
        `task_id` criados).
    """
    try:
        task = await background_tasks.get_task(task_id)
        if task is None:
            return json.dumps(
                {"status": "error", "error": f"task {task_id!r} não existe"}
            )
        if task.status != "triage":
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        f"task {task_id!r} não está em triage "
                        f"(status atual: {task.status!r})"
                    ),
                }
            )

        from backend.llm.fallback_chat_client import FallbackChatClient
        from backend.vtypes.message import ContentBlock, MessageRole, VMessage

        model_id = ctx.model

        llm = FallbackChatClient(primary_model_id=model_id)
        resposta = await llm.agenerate(
            [
                VMessage(
                    role=MessageRole.SYSTEM,
                    content=[ContentBlock(kind="text", text=_DECOMPOSE_SYSTEM_PROMPT)],
                ),
                VMessage(
                    role=MessageRole.USER,
                    content=[
                        ContentBlock(
                            kind="text",
                            text=f"Task name: {task.name}\nInstruction: {task.instruction}",
                        )
                    ],
                ),
            ]
        )
        grafo = _parse_decomposition(resposta.text())
        if not grafo:
            return json.dumps(
                {
                    "status": "ok",
                    "decomposed": False,
                    "reason": "modelo não propôs decomposição válida — card segue em triage",
                }
            )

        created_ids: list[str] = []
        for no in grafo:
            child = await background_tasks.create_task(
                session_id=task.session_id,
                user_id=task.user_id,
                kind=task.kind,
                name=no["name"],
                instruction=no["instruction"],
                trigger_type="manual",
                workspace_id=task.workspace_id,
                agent_profile_id=task.agent_profile_id,
            )
            created_ids.append(child.id)

        for idx, no in enumerate(grafo):
            tem_pai_valido = False
            for pai_idx in no["parents"]:
                if pai_idx == idx or not (0 <= pai_idx < len(created_ids)):
                    continue
                try:
                    await kanban.add_dependency(created_ids[pai_idx], created_ids[idx])
                    tem_pai_valido = True
                except ValueError:
                    logger.warning(
                        "kanban_decompose: dependência inválida (ciclo) ignorada",
                        extra={
                            "task_id": task_id,
                            "child_idx": idx,
                            "parent_idx": pai_idx,
                        },
                    )
            if tem_pai_valido:
                await kanban.set_status(created_ids[idx], "todo")

        await kanban.set_status(task_id, "archived")

        return json.dumps({"status": "ok", "decomposed": True, "children": created_ids})
    except Exception as e:
        logger.exception(
            "kanban_decompose: erro inesperado", extra={"task_id": task_id}
        )
        return json.dumps({"status": "error", "error": str(e)})
