"""Background Memory Consolidation.

Job periódico que lê as últimas 10 threads do usuário, sintetiza via LLM e
atualiza as seções de memória de longo prazo em
``~/.vectora/memory/{decisions,gotchas,preferences}.md`` — injetadas
automaticamente no contexto do agente por ``_agents_md_paths()`` em
``agent_factory.py``.

Cada seção é versionada: antes de sobrescrever, o conteúdo anterior é
arquivado em ``~/.vectora/memory/.history/<timestamp>-<seção>.md`` (nunca
perdido silenciosamente) e a escrita em si é atômica (arquivo temporário +
rename, nunca trunca o arquivo no lugar).

Gate de aprovação (``settings.memory_consolidation_require_approval``,
default ``True`` — mesma semântica do ``[auto_improve] require_approval``
do ai-memory): em vez de escrever direto, a consolidação propõe a mudança
como artifact (mesmo mecanismo HITL de ``install_learned_skill``/
``save_learned_fact``) anexado à thread mais recente do usuário — só é
persistida quando o usuário aprova via ``apply_memory_consolidation``
(``backend/tools/learning.py``).

Operação best-effort: qualquer falha é registrada em log e ignorada
para não impactar o fluxo principal.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.settings import settings

logger = logging.getLogger(__name__)

_MAX_THREADS = 10
_MSG_PREVIEW_CHARS = 400

#: Categorias de seção — mesmo vocabulário de `FactCategory`
#: (`backend/tools/memory.py`), exceto `rule` (não faz sentido como seção
#: de síntese de conversas — fica reservada a fatos individuais).
CONSOLIDATION_CATEGORIES: tuple[str, ...] = ("decisions", "gotchas", "preferences")

_PROMPT_TEMPLATE = """\
Você é um assistente que sintetiza memória de conversas passadas.

Abaixo estão os resumos das últimas conversas do usuário:

{threads_text}

Organize sua resposta em até três seções markdown, com exatamente estes
cabeçalhos (em inglês, minúsculo), nesta ordem — omita uma seção se não
houver nada relevante para ela:

## decisions
(decisões técnicas/de produto que o usuário tomou)

## gotchas
(armadilhas, erros recorrentes, coisas que já custaram tempo)

## preferences
(preferências de estilo, ferramentas, forma de trabalhar)

Cada seção: bullet points concisos (máx. 400 palavras no total), só
informações factuais e úteis para o futuro, sem repetição. Escreva o
conteúdo das seções em português.
"""

_CATEGORY_HEADER_RE = re.compile(
    r"^##\s*(decisions|gotchas|preferences)\s*$", re.IGNORECASE | re.MULTILINE
)


# ---------------------------------------------------------------------------
# Helpers internos (mockáveis em testes)
# ---------------------------------------------------------------------------


def memory_dir() -> Path:
    return settings.vectora_home / "memory"


def section_path(category: str, base_dir: Path | None = None) -> Path:
    return (base_dir or memory_dir()) / f"{category}.md"


async def _fetch_recent_threads(
    user_id: str,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Últimas `_MAX_THREADS` threads do usuário, mais recente primeiro —
    cada item é `(thread_id, [(role, text), ...])`."""
    try:
        from backend.api.handlers.threads import _get_db

        db = await _get_db()
        rows = await db.execute_fetchall(
            "SELECT thread_id FROM vectora_sessions WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, _MAX_THREADS),
        )
        if not rows:
            return []

        from backend.services.agent_factory import aget_thread_messages

        threads: list[tuple[str, list[tuple[str, str]]]] = []
        for row in rows:
            thread_id = row[0]
            try:
                messages = await aget_thread_messages(thread_id)
                if messages:
                    threads.append(
                        (
                            thread_id,
                            [(role, text) for role, text, _cp, _att in messages],
                        )
                    )
            except Exception:
                logger.debug("memory_consolidation: falha ao ler thread=%s", thread_id)
        return threads
    except Exception:
        logger.exception("memory_consolidation: _fetch_recent_threads falhou")
        return []


async def _invoke_llm(prompt: str) -> Any:
    """Invoca o LLM padrão com o prompt de consolidação — retorna VMessage nativo."""
    from backend.services.utils import load_native_llm
    from backend.vtypes.message import MessageRole, text_message

    llm = load_native_llm()
    return await llm.agenerate([text_message(MessageRole.USER, prompt)])


# ---------------------------------------------------------------------------
# Funções públicas (testáveis)
# ---------------------------------------------------------------------------


def _build_consolidation_prompt(
    threads: list[list[tuple[str, str]]],
) -> str:
    """Constrói o prompt de síntese a partir de uma lista de threads."""
    if not threads:
        return _PROMPT_TEMPLATE.format(threads_text="(sem conversas recentes)")

    parts: list[str] = []
    for i, messages in enumerate(threads, 1):
        lines: list[str] = []
        for role, text in messages:
            prefix = "Usuário" if role == "human" else "Agente"
            truncated = text[:_MSG_PREVIEW_CHARS]
            if len(text) > _MSG_PREVIEW_CHARS:
                truncated += "…"
            lines.append(f"{prefix}: {truncated}")
        parts.append(f"--- Conversa {i} ---\n" + "\n".join(lines))

    return _PROMPT_TEMPLATE.format(threads_text="\n\n".join(parts))


def _parse_llm_output(raw: str) -> str:
    """Remove markdown fences e normaliza espaços."""
    text = raw.strip()
    if not text:
        return ""
    # Remove ``` fences (```markdown, ```md, etc.)
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove primeira linha (```...`) e última (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text


def split_by_category(text: str) -> dict[str, str]:
    """Divide a saída do LLM nas seções `## decisions`/`## gotchas`/
    `## preferences`. Cabeçalho sem conteúdo (ou ausente) simplesmente não
    entra no dict — nunca gera seção vazia."""
    sections: dict[str, str] = {}
    matches = list(_CATEGORY_HEADER_RE.finditer(text))
    for i, match in enumerate(matches):
        category = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            sections[category] = content
    return sections


def _write_atomic(path: Path, content: str) -> None:
    """Escreve via arquivo temporário + rename — nunca deixa `path` truncado
    a meio caminho se o processo morrer no meio da escrita."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _archive_previous(category: str, previous_content: str, base_dir: Path) -> None:
    if not previous_content.strip():
        return
    history_dir = base_dir / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    archive_path = history_dir / f"{timestamp}-{category}.md"
    archive_path.write_text(previous_content, encoding="utf-8")


def apply_consolidation_sections(
    sections: dict[str, str], base_dir: Path | None = None
) -> list[str]:
    """Grava cada seção não vazia em `base_dir/{categoria}.md`, arquivando a
    versão anterior antes de sobrescrever — só quando o conteúdo realmente
    muda (comparação após strip; rodada idêntica não gera entrada de
    histórico redundante). Devolve as categorias efetivamente alteradas.
    """
    target_dir = base_dir or memory_dir()
    changed: list[str] = []
    for category, new_content in sections.items():
        if category not in CONSOLIDATION_CATEGORIES:
            continue
        path = section_path(category, target_dir)
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        if previous.strip() == new_content.strip():
            continue
        _archive_previous(category, previous, target_dir)
        _write_atomic(path, new_content.strip() + "\n")
        changed.append(category)
    return changed


async def _propose_consolidation(thread_id: str, sections: dict[str, str]) -> None:
    """Grava a proposta de consolidação como artifact na thread mais
    recente do usuário — mesmo padrão do Remember (`remember_trigger.py`):
    fica visível na aba Plan, só é persistida quando o usuário aprova via
    `apply_memory_consolidation`."""
    from backend.tools.context import ToolContext
    from backend.tools.fs import create_artifact

    lines = ["# Proposta de consolidação de memória", ""]
    for category in CONSOLIDATION_CATEGORIES:
        content = sections.get(category)
        if not content:
            continue
        lines.append(f"## {category}")
        lines.append(content)
        lines.append("")
    lines.append(
        "Peça ao agente para aplicar a categoria que quiser manter "
        "(`apply_memory_consolidation`) — cada uma pede sua própria "
        "aprovação antes de gravar."
    )
    body = "\n".join(lines)

    await create_artifact(
        artifact_type="memory_consolidation_proposal",
        title="Proposta de consolidação de memória",
        content=body,
        ctx=ToolContext(thread_id=thread_id),
    )


async def consolidate_memory(user_id: str) -> None:
    """Sintetiza as últimas threads e atualiza as seções de memória de
    longo prazo (decisions/gotchas/preferences).

    Best-effort: qualquer exceção é capturada e logada sem propagar.
    """
    try:
        if not settings.configured_llm_providers():
            logger.debug(
                "memory_consolidation: nenhum provider de LLM configurado — "
                "job periódico pulado user=%s",
                user_id,
            )
            return

        threads = await _fetch_recent_threads(user_id)
        if not threads:
            logger.debug("memory_consolidation: sem threads para user=%s", user_id)
            return

        most_recent_thread_id = threads[0][0]
        message_lists = [messages for _thread_id, messages in threads]

        prompt = _build_consolidation_prompt(message_lists)
        response = await _invoke_llm(prompt)
        raw = _parse_llm_output(response.text())

        if not raw:
            logger.warning(
                "memory_consolidation: LLM retornou conteúdo vazio user=%s", user_id
            )
            return

        sections = split_by_category(raw)
        if not sections:
            logger.warning(
                "memory_consolidation: nenhuma seção reconhecida na saída "
                "do LLM user=%s",
                user_id,
            )
            return

        if settings.memory_consolidation_require_approval:
            await _propose_consolidation(most_recent_thread_id, sections)
            logger.info(
                "memory_consolidation: proposta gravada (aguarda aprovação) "
                "user=%s categorias=%s",
                user_id,
                list(sections),
            )
        else:
            changed = apply_consolidation_sections(sections)
            logger.info(
                "memory_consolidation: seções atualizadas diretamente user=%s "
                "categorias=%s",
                user_id,
                changed,
            )
    except Exception:
        logger.exception("memory_consolidation: falha inesperada user=%s", user_id)


async def run_consolidation_for_all_users() -> None:
    """Dispara consolidação para todos os usuários ativos (últimos 7 dias).

    Chamado pelo scheduler a cada 6 horas.
    """
    try:
        from datetime import timedelta

        from backend.services import agent_factory

        store = await agent_factory.get_session_store()
        since_iso = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        users = await store.list_active_user_ids(since_iso)
        logger.info("memory_consolidation: iniciando para %d usuários", len(users))
        for user_id in users:
            await consolidate_memory(user_id)
    except Exception:
        logger.exception("memory_consolidation: run_consolidation_for_all_users falhou")
