"""Remember — gatilho automático a cada N turnos de conversa.

Disparado fire-and-forget ao final de cada turno completo do agente
(``backend/api/native_stream.py``, quando ``stopped_reason == "stop"``). Nunca aplica
skill/fato sozinho — só destila o transcript e, se houver algo reaproveitável,
grava a proposta como artifact (``create_artifact``, tipo ``remember_proposal``)
pra aparecer na aba Plan na próxima interação. Uma proposta pendente não
resolvida bloqueia um novo gatilho automático até ``install_learned_skill``
ou ``save_learned_fact`` resolverem (chamados pelo usuário a partir da
própria proposta) e limparem a flag de pendência.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

REMEMBER_TRIGGER_EVERY_N_TURNS = 5


async def maybe_trigger_remember(thread_id: str, user_id: str) -> None:
    """Incrementa o contador de turnos da thread; a cada N turnos, sem
    proposta pendente, destila o transcript e propõe skills/fatos.

    Best-effort: qualquer falha aqui não pode afetar o turno de chat que
    já terminou — é sempre chamada fire-and-forget, nunca ``await``ada
    pelo caller.
    """
    try:
        from backend.api.handlers.threads import (
            get_remember_pending,
            increment_remember_turn_count,
            set_remember_pending,
        )

        count = await increment_remember_turn_count(thread_id)
        if count % REMEMBER_TRIGGER_EVERY_N_TURNS != 0:
            return
        if await get_remember_pending(thread_id):
            logger.debug(
                "remember_trigger: proposta pendente ainda não resolvida "
                "thread=%s — gatilho automático adiado",
                thread_id,
            )
            return

        # Gatilho automático, sem usuário no loop — nunca pode chamar uma
        # LLM só porque `load_native_llm()` resolveria pro provider de
        # fábrica (nunca escolhido pelo usuário, ex.: instalação recém-
        # extraída ainda sem setup). Sem nenhuma credencial configurada,
        # a chamada abaixo falharia de qualquer forma; a diferença é não
        # tentar às cegas.
        from backend.settings import settings

        if not settings.configured_llm_providers():
            logger.debug(
                "remember_trigger: nenhum provider de LLM configurado — "
                "gatilho automático pulado thread=%s",
                thread_id,
            )
            return

        from backend.services import agent_factory
        from backend.services.learning import (
            dedupe_fact_drafts,
            dedupe_skill_drafts,
            distill_transcript,
        )
        from backend.tools.memory import list_fact_contents
        from backend.workspace.skills import list_skills

        pairs = await agent_factory.aget_thread_messages(thread_id)
        transcript = "\n".join(f"{role}: {text}" for role, text, _, _att in pairs)
        if not transcript.strip():
            return

        result = await distill_transcript(transcript)
        existing_names = {s.name for s in list_skills(user_id)}
        skills = dedupe_skill_drafts(result.skills, existing_names)
        existing_facts = await list_fact_contents(user_id)
        facts = dedupe_fact_drafts(result.facts, existing_facts)

        if not skills and not facts:
            logger.debug(
                "remember_trigger: nada reaproveitável thread=%s (turno %d)",
                thread_id,
                count,
            )
            return

        await set_remember_pending(thread_id, True)
        await _write_proposal_artifact(thread_id, skills, facts)
        logger.info(
            "remember_trigger: proposta automática gravada thread=%s "
            "skills=%d facts=%d",
            thread_id,
            len(skills),
            len(facts),
        )
    except Exception:
        logger.warning(
            "remember_trigger: falha no gatilho automático (não afeta o turno)",
            exc_info=True,
        )


async def _write_proposal_artifact(
    thread_id: str, skills: list, facts: list[str]
) -> None:
    from backend.tools.context import ToolContext
    from backend.tools.fs import create_artifact

    lines = ["# Proposta automática do Remember", ""]
    if skills:
        lines.append("## Skills propostas")
        lines.extend(f"- **{s.name}** — {s.description}" for s in skills)
        lines.append("")
    if facts:
        lines.append("## Fatos propostos")
        lines.extend(f"- {fact}" for fact in facts)
        lines.append("")
    lines.append(
        "Peça ao agente para instalar a skill (`install_learned_skill`) ou "
        "salvar o fato (`save_learned_fact`) que quiser manter — cada um "
        "pede sua própria aprovação antes de gravar."
    )
    content = "\n".join(lines)

    await create_artifact(
        artifact_type="remember_proposal",
        title="Proposta do Remember",
        content=content,
        ctx=ToolContext(thread_id=thread_id),
    )
