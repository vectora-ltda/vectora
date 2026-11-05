"""Remember — learning loop: destila um transcript de thread em skills
reutilizáveis e fatos duráveis sobre o usuário, via LLM com saída
estruturada. Nada aqui persiste em disco/BaseStore — só produz o
`DistillationResult` que o caller (tool `learn_from_session`) devolve ao
agente, que por sua vez pede aprovação HITL antes de gravar qualquer coisa
(`install_learned_skill`, `save_memory`).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DISTILL_SYSTEM_PROMPT = (
    "Você analisa o transcript de uma conversa entre um usuário e um "
    "agente de IA e extrai APENAS o que vale a pena lembrar para sessões "
    "futuras:\n\n"
    "1. Skills reutilizáveis: padrões de solução que se repetiriam em "
    "outras tarefas semelhantes (ex.: um procedimento de debug, um "
    "template de configuração). Cada skill precisa de um nome curto, uma "
    "descrição de quando usá-la, e o conteúdo (passo a passo) em "
    "Markdown.\n"
    "2. Fatos duráveis sobre o usuário ou o projeto: preferências, "
    "convenções, contexto que não muda a cada sessão.\n\n"
    "Se o transcript não tiver nenhum padrão reutilizável ou fato "
    "durável, devolva listas vazias — isso é um resultado válido, não "
    "uma falha. Nunca invente uma skill genérica só para preencher a "
    "lista."
)


class SkillDraft(BaseModel):
    name: str
    description: str
    content: str


class DistillationResult(BaseModel):
    skills: list[SkillDraft] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)


async def distill_transcript(transcript_text: str) -> DistillationResult:
    """Extrai skills/fatos do `transcript_text` via LLM com saída JSON.

    Defensivo: transcript vazio ou falha do LLM (rede, parse, timeout)
    devolve `DistillationResult()` vazio — nunca propaga exceção pro
    caller (tool `learn_from_session`). Sem structured output nativo no
    ChatClient, pedimos JSON no prompt e validamos com
    `DistillationResult.model_validate`.
    """
    if not transcript_text.strip():
        return DistillationResult()

    try:
        from backend.services.utils import load_native_llm
        from backend.vtypes.message import MessageRole, text_message

        llm = load_native_llm()
        system = (
            _DISTILL_SYSTEM_PROMPT
            + "\n\nResponda APENAS com JSON válido, sem texto fora do JSON, no formato:\n"
            '{"skills": [{"name": "...", "description": "...", "content": "..."}], '
            '"facts": ["..."]}'
        )
        result = await llm.agenerate(
            [
                text_message(MessageRole.SYSTEM, system),
                text_message(MessageRole.USER, transcript_text[:20000]),
            ]
        )
        return _parse_distillation(result.text())
    except Exception as exc:
        logger.warning("learning: falha ao destilar transcript — %s", exc)
        return DistillationResult()


def _parse_distillation(raw: str) -> DistillationResult:
    """Extrai o JSON da resposta do LLM (tolerando ```json fences) e valida."""
    import json

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json")
        text = text.strip()
    data = json.loads(text)
    return DistillationResult.model_validate(data)


def dedupe_skill_drafts(
    drafts: list[SkillDraft], existing_names: set[str]
) -> list[SkillDraft]:
    """Remove drafts cujo nome normalizado já existe entre as skills
    instaladas — evita duplicar/pedir aprovação pra algo já aprendido."""
    normalized_existing = {name.strip().lower() for name in existing_names}
    return [
        draft
        for draft in drafts
        if draft.name.strip().lower() not in normalized_existing
    ]


def dedupe_fact_drafts(facts: list[str], existing_contents: list[str]) -> list[str]:
    """Remove fatos cujo conteúdo normalizado já foi salvo antes — mesma
    paridade de dedup que `dedupe_skill_drafts` já dava às skills, evitando
    que o Remember proponha de novo (em sessões futuras) um fato que o
    usuário já aprovou. Normalização é só strip+lower nas pontas (não
    colapsa espaços internos), igual ao dedup de skill."""
    normalized_existing = {content.strip().lower() for content in existing_contents}
    seen: set[str] = set()
    kept: list[str] = []
    for fact in facts:
        key = fact.strip().lower()
        if key in normalized_existing or key in seen:
            continue
        seen.add(key)
        kept.append(fact)
    return kept
