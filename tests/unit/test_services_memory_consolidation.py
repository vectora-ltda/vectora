"""Background Memory Consolidation.

Job periódico que sintetiza as últimas 10 threads via LLM e atualiza as
seções de memória de longo prazo (decisions/gotchas/preferences) em
``~/.vectora/memory/`` — versionadas (histórico) e gated por aprovação
(``memory_consolidation_require_approval``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduling.memory_consolidation import (
    CONSOLIDATION_CATEGORIES,
    _build_consolidation_prompt,
    _parse_llm_output,
    apply_consolidation_sections,
    consolidate_memory,
    section_path,
    split_by_category,
)
from backend.settings import Settings

# ---------------------------------------------------------------------------
# _build_consolidation_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_includes_all_threads():
    """O prompt deve mencionar cada thread passada."""
    threads = [
        [("human", "oi"), ("assistant", "olá")],
        [("human", "erro no auth"), ("assistant", "corrigi o JWT")],
    ]
    prompt = _build_consolidation_prompt(threads)
    assert "oi" in prompt
    assert "corrigi o JWT" in prompt


def test_build_prompt_empty_threads_returns_valid_string():
    """Sem threads, deve retornar string não-vazia (template mínimo)."""
    prompt = _build_consolidation_prompt([])
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_build_prompt_caps_per_thread():
    """Cada thread não deve ultrapassar ~2000 chars no prompt (evita tokens demais)."""
    long_msg = "a" * 3000
    threads = [
        [("human", long_msg), ("assistant", long_msg)],
    ]
    prompt = _build_consolidation_prompt(threads)
    # Garante que o prompt não explodiu — há sempre uma redução
    assert len(prompt) < len(long_msg) * 2 + 5000


# ---------------------------------------------------------------------------
# _parse_llm_output
# ---------------------------------------------------------------------------


def test_parse_llm_output_returns_stripped_text():
    raw = "  Aprendi que o projeto usa SQLite.  "
    result = _parse_llm_output(raw)
    assert result == "Aprendi que o projeto usa SQLite."


def test_parse_llm_output_strips_markdown_fences():
    raw = "```markdown\n# Memória\nConteúdo\n```"
    result = _parse_llm_output(raw)
    assert "```" not in result
    assert "Conteúdo" in result


def test_parse_llm_output_empty_returns_empty():
    assert _parse_llm_output("") == ""
    assert _parse_llm_output("   ") == ""


# ---------------------------------------------------------------------------
# split_by_category
# ---------------------------------------------------------------------------


class TestSplitByCategory:
    def test_parses_all_three_sections(self):
        text = (
            "## decisions\nUsar SQLite.\n\n"
            "## gotchas\nJWT expira em 15min.\n\n"
            "## preferences\nRespostas curtas."
        )
        sections = split_by_category(text)
        assert sections == {
            "decisions": "Usar SQLite.",
            "gotchas": "JWT expira em 15min.",
            "preferences": "Respostas curtas.",
        }

    def test_missing_section_is_simply_absent(self):
        """LLM que só produz decisions não inventa gotchas/preferences vazias."""
        text = "## decisions\nUsar Postgres."
        sections = split_by_category(text)
        assert sections == {"decisions": "Usar Postgres."}
        assert "gotchas" not in sections
        assert "preferences" not in sections

    def test_header_without_content_is_dropped(self):
        """Cabeçalho seguido de nada (ou só espaço) não vira seção vazia."""
        text = "## decisions\n\n## gotchas\nConteúdo real."
        sections = split_by_category(text)
        assert "decisions" not in sections
        assert sections["gotchas"] == "Conteúdo real."

    def test_text_without_any_known_header_returns_empty_dict(self):
        """Saída do LLM que não segue o formato esperado não quebra — dict vazio."""
        assert split_by_category("Só um parágrafo solto, sem cabeçalho.") == {}


# ---------------------------------------------------------------------------
# apply_consolidation_sections
# ---------------------------------------------------------------------------


class TestApplyConsolidationSections:
    def test_writes_section_and_archives_previous_version(self, tmp_path: Path):
        """Rodada com conteúdo novo grava a seção certa e preserva a versão
        anterior no histórico (regressão do bug original: sobrescrita sem
        rastro)."""
        path = section_path("decisions", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Versão antiga.", encoding="utf-8")

        changed = apply_consolidation_sections({"decisions": "Versão nova."}, tmp_path)

        assert changed == ["decisions"]
        assert path.read_text(encoding="utf-8").strip() == "Versão nova."
        history_files = list((tmp_path / ".history").glob("*-decisions.md"))
        assert len(history_files) == 1
        assert history_files[0].read_text(encoding="utf-8") == "Versão antiga."

    def test_unchanged_content_does_not_touch_history(self, tmp_path: Path):
        """Segunda rodada sem mudança real de conteúdo não gera entrada de
        histórico redundante."""
        path = section_path("gotchas", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Mesmo conteúdo.", encoding="utf-8")

        changed = apply_consolidation_sections({"gotchas": "Mesmo conteúdo."}, tmp_path)

        assert changed == []
        history_dir = tmp_path / ".history"
        assert not history_dir.exists() or not list(history_dir.glob("*"))

    def test_unknown_category_is_ignored(self, tmp_path: Path):
        """Categoria fora de CONSOLIDATION_CATEGORIES nunca vira arquivo —
        proteção contra o LLM inventar um cabeçalho não previsto."""
        changed = apply_consolidation_sections(
            {"random_category": "conteúdo"}, tmp_path
        )
        assert changed == []
        assert not any(tmp_path.iterdir())

    def test_recovering_history_across_two_different_rounds(self, tmp_path: Path):
        """Regressão explícita: rodar consolidação duas vezes com conteúdos
        diferentes deixa a primeira versão recuperável do histórico (o bug
        original — sobrescrita sem rastro — teria feito isso falhar)."""
        apply_consolidation_sections({"preferences": "Primeira versão."}, tmp_path)
        apply_consolidation_sections({"preferences": "Segunda versão."}, tmp_path)

        path = section_path("preferences", tmp_path)
        assert path.read_text(encoding="utf-8").strip() == "Segunda versão."
        history_files = list((tmp_path / ".history").glob("*-preferences.md"))
        assert len(history_files) == 1
        assert (
            history_files[0].read_text(encoding="utf-8").strip() == "Primeira versão."
        )

    def test_all_categories_covered(self):
        assert set(CONSOLIDATION_CATEGORIES) == {
            "decisions",
            "gotchas",
            "preferences",
        }


# ---------------------------------------------------------------------------
# consolidate_memory
# ---------------------------------------------------------------------------


def _fake_llm_response(content: str):
    from backend.vtypes.message import MessageRole, text_message

    return text_message(MessageRole.ASSISTANT, content)


@pytest.mark.asyncio
async def test_consolidate_memory_no_threads_skips_llm():
    """Sem threads recentes, não deve chamar o LLM."""
    with (
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(),
        ) as mock_llm,
    ):
        await consolidate_memory(user_id="user-1")

    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_consolidate_memory_skips_without_provider_but_runs_with_one(
    monkeypatch,
):
    """Job periódico, sem usuário no loop — nunca pode chamar o LLM sem
    nenhum provider configurado (ex.: instalação recém-extraída ainda sem
    setup). Caso de borda no mesmo teste: com 1 provider, roda normal."""
    fetch = AsyncMock(return_value=[("t1", [("human", "hi"), ("assistant", "hello")])])
    with (
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=fetch,
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(),
        ) as mock_llm,
    ):
        monkeypatch.setattr(Settings, "configured_llm_providers", lambda self: [])
        await consolidate_memory(user_id="user-1")
        mock_llm.assert_not_called()
        fetch.assert_not_called()

        monkeypatch.setattr(
            Settings, "configured_llm_providers", lambda self: ["google-genai"]
        )
        await consolidate_memory(user_id="user-1")
        mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_consolidate_memory_llm_failure_does_not_raise():
    """Falha do LLM não deve propagar — é best-effort."""
    with (
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=AsyncMock(
                return_value=[("t1", [("human", "hi"), ("assistant", "hello")])]
            ),
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(side_effect=RuntimeError("LLM unavailable")),
        ),
    ):
        await consolidate_memory(user_id="user-1")  # must not raise


@pytest.mark.asyncio
async def test_consolidate_memory_output_without_known_headers_is_ignored():
    """LLM que não segue o formato de seções esperado não escreve nada nem
    propõe artifact — apenas loga e retorna."""
    with (
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=AsyncMock(
                return_value=[("t1", [("human", "hi"), ("assistant", "hello")])]
            ),
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(return_value=_fake_llm_response("texto solto sem cabeçalho")),
        ),
        patch(
            "backend.scheduling.memory_consolidation._propose_consolidation",
            new=AsyncMock(),
        ) as mock_propose,
        patch(
            "backend.scheduling.memory_consolidation.apply_consolidation_sections"
        ) as mock_apply,
    ):
        await consolidate_memory(user_id="user-1")

    mock_propose.assert_not_called()
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_consolidate_memory_proposes_when_approval_required():
    """`require_approval=True` (default) propõe via artifact em vez de
    escrever direto — dá o mesmo tratamento HITL de `save_learned_fact`."""
    with (
        patch("backend.settings.settings.memory_consolidation_require_approval", True),
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=AsyncMock(return_value=[("thread-42", [("human", "fix auth")])]),
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(return_value=_fake_llm_response("## decisions\nUsar JWT.")),
        ),
        patch(
            "backend.scheduling.memory_consolidation._propose_consolidation",
            new=AsyncMock(),
        ) as mock_propose,
        patch(
            "backend.scheduling.memory_consolidation.apply_consolidation_sections"
        ) as mock_apply,
    ):
        await consolidate_memory(user_id="user-1")

    mock_propose.assert_called_once_with("thread-42", {"decisions": "Usar JWT."})
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_consolidate_memory_writes_directly_when_approval_not_required(
    tmp_path: Path,
):
    """`require_approval=False` grava direto (comportamento anterior, sem
    a etapa de proposta)."""
    with (
        patch(
            "backend.settings.settings.memory_consolidation_require_approval",
            False,
        ),
        patch(
            "backend.scheduling.memory_consolidation._fetch_recent_threads",
            new=AsyncMock(return_value=[("thread-1", [("human", "fix auth")])]),
        ),
        patch(
            "backend.scheduling.memory_consolidation._invoke_llm",
            new=AsyncMock(
                return_value=_fake_llm_response("## gotchas\nJWT expira rápido.")
            ),
        ),
        patch(
            "backend.scheduling.memory_consolidation.memory_dir",
            return_value=tmp_path,
        ),
        patch(
            "backend.scheduling.memory_consolidation._propose_consolidation",
            new=AsyncMock(),
        ) as mock_propose,
    ):
        await consolidate_memory(user_id="user-1")

    mock_propose.assert_not_called()
    path = section_path("gotchas", tmp_path)
    assert path.exists()
    assert "JWT expira rápido." in path.read_text(encoding="utf-8")


class TestRunConsolidationForAllUsers:
    """``run_consolidation_for_all_users`` — dispara consolidação pra todo
    usuário com atividade recente, lendo de ``SessionStore`` (fonte real de
    ``user_id``, não ``vectora_sessions`` — regressão: essa função consultava
    colunas que nunca existiram em ``vectora_sessions``, falhando toda vez
    que o scheduler rodava)."""

    async def test_consolida_cada_usuario_ativo_encontrado(self):
        from backend.scheduling.memory_consolidation import (
            run_consolidation_for_all_users,
        )

        fake_store = MagicMock()
        fake_store.list_active_user_ids = AsyncMock(return_value=["alice", "bob"])

        with (
            patch(
                "backend.services.agent_factory.get_session_store",
                new=AsyncMock(return_value=fake_store),
            ),
            patch(
                "backend.scheduling.memory_consolidation.consolidate_memory",
                new=AsyncMock(),
            ) as mock_consolidate,
        ):
            await run_consolidation_for_all_users()

        called_users = [c.args[0] for c in mock_consolidate.await_args_list]
        assert called_users == ["alice", "bob"]

    async def test_falha_ao_consultar_store_nao_propaga(self):
        """Erro/borda: operação best-effort — falha na consulta ao
        SessionStore nunca derruba o scheduler."""
        from backend.scheduling.memory_consolidation import (
            run_consolidation_for_all_users,
        )

        with patch(
            "backend.services.agent_factory.get_session_store",
            new=AsyncMock(side_effect=RuntimeError("banco indisponível")),
        ):
            try:
                await run_consolidation_for_all_users()
            except Exception:
                pytest.fail(
                    "run_consolidation_for_all_users propagou exceção — "
                    "deve ser best-effort"
                )
