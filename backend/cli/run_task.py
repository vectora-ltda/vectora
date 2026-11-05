"""``vectora run`` — sobe o motor nativo, roda uma tarefa uma única vez e sai.

Modo one-shot pensado para ambientes efêmeros (CI, o Vectora Bot for GHA):
sem servidor FastAPI, sem HITL interativo (não existe UI de aprovação num
runner de CI — ``permission_mode="auto"``, mesmo motivo/valor já usado por
``backend/services/connect/runner.py`` para mensageria externa) e sem
estado persistente entre execuções — a menos que ``VECTORA_HOME`` já esteja
setado no ambiente, cada chamada usa um diretório temporário descartado ao
sair, para não escrever sessões numa instalação local real.

Reusa exatamente o mesmo caminho de montagem do motor que o chat web usa
(``agent_factory.get_native_agent``/``get_session_store``) e o mesmo padrão
de invocação de ``run_conversation`` que ``connect/runner.py`` já usa para
plataformas externas — só troca "de onde vem o texto" e "para onde vai a
resposta" (stdin/argumento → stdout, em vez de Telegram/Discord/etc.).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from uuid import uuid4

#: Mesmo valor/motivo de `connect/runner.py::_EXTERNAL_PERMISSION_MODE` —
#: um runner de CI não tem UI de aprovar/rejeitar tool call.
_PERMISSION_MODE = "auto"


def _read_task(args: argparse.Namespace) -> str:
    if args.task:
        return args.task
    if sys.stdin.isatty():
        print(
            'Uso: vectora run --task "..." (ou envie a tarefa via stdin)',
            file=sys.stderr,
        )
        sys.exit(1)
    return sys.stdin.read().strip()


def run_run_task(args: argparse.Namespace) -> None:
    """Ponto de entrada síncrono do subcomando ``run`` — ver módulo."""
    task = _read_task(args)
    if not task:
        print("Nenhuma tarefa fornecida.", file=sys.stderr)
        sys.exit(1)

    # Não existe "modelo padrão" resolvível no lado do servidor — o valor
    # mostrado na UI do chat vem do estado do FRONTEND (localStorage),
    # enviado explicitamente em toda requisição; `settings.default_model`
    # é escrito pelo admin mas nunca lido por nenhum caminho de execução.
    # Sem frontend nenhum aqui, o modelo tem que vir explícito.
    model_id = args.model or os.environ.get("VECTORA_MODEL", "")
    if not model_id:
        print(
            "Nenhum modelo especificado. Use --model provider:model-id ou "
            "defina a env var VECTORA_MODEL (ex.: google_genai:gemini-3-flash).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Precisa rodar ANTES do primeiro import de `backend.settings` (aqui,
    # transitivo via `_run_task` abaixo) — os paths de storage são
    # computados a partir de `VECTORA_HOME` no momento do import, mesmo
    # invariante documentado em `tests/conftest.py::pytest_configure`.
    if not os.environ.get("VECTORA_HOME"):
        os.environ["VECTORA_HOME"] = tempfile.mkdtemp(prefix="vectora-run-")

    exit_code = asyncio.run(_run_task(task, model_id=model_id))
    sys.exit(exit_code)


async def _run_task(task: str, *, model_id: str) -> int:
    from backend.engine.conversation_loop import LoopConfig, run_conversation
    from backend.engine.hitl import should_require_approval
    from backend.llm.fallback_chat_client import FallbackChatClient
    from backend.services import agent_factory
    from backend.vtypes.context import ctx_from_config
    from backend.vtypes.message import MessageRole, text_message

    thread_id = f"cli-run-{uuid4().hex}"

    try:
        native_agent = await agent_factory.get_native_agent(
            user_id="local", chat_mode=False, workspace_id=None
        )
        session_store = await agent_factory.get_session_store()
        approval_gate = await agent_factory.get_approval_gate()

        await session_store.create_session(
            thread_id,
            user_id="local",
            mode="cli",
            permission_mode=_PERMISSION_MODE,
        )
        parent_id = await session_store.append_message(
            thread_id,
            text_message(MessageRole.SYSTEM, native_agent.system_prompt),
        )
        await session_store.append_message(
            thread_id,
            text_message(MessageRole.USER, task),
            parent_message_id=parent_id,
        )

        configurable = {
            "thread_id": thread_id,
            "user_id": "local",
            "permission_mode": _PERMISSION_MODE,
        }
        run_ctx = ctx_from_config({"configurable": configurable})
        run_ctx.store = await agent_factory.get_store()
        chat_client = FallbackChatClient(primary_model_id=model_id)
        loop_config = LoopConfig(max_iterations=50)

        if native_agent.subagent_catalog:
            from backend.tools.subagent_delegate import SubagentDeps

            run_ctx._extra["subagent_deps"] = SubagentDeps(
                catalog=native_agent.subagent_catalog,
                session_store=session_store,
                chat_client=chat_client,
                config=loop_config,
                should_require_approval=should_require_approval,
            )

        result = await run_conversation(
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=native_agent.tool_registry,
            ctx=run_ctx,
            thread_id=thread_id,
            config=loop_config,
            should_require_approval=should_require_approval,
            approval_gate=approval_gate,
        )
    finally:
        await agent_factory.aclose()

    reply = result.final_message.text() if result.final_message else ""
    print(reply)
    return 0 if result.stopped_reason == "stop" else 1
