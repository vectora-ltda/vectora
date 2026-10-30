"""Aprovação inteligente de comandos — avaliador auxiliar + allowlist.

Invariante crítico, o mesmo em todos os testes deste arquivo: esta camada
**nunca** substitui o HITL sozinha. `evaluate_command` só devolve um booleano
de anotação (`pre_approved`) — quem decide se a tool executa continua sendo
`REQUIRE_APPROVAL`/`should_require_approval` (`backend/engine/hitl.py`), que
nem sabe que esta camada existe. Mais conservador que o Hermes (que
auto-executa), alinhado ao HITL sempre já vinculante do produto.

Cada caminho feliz tem o par de erro/borda no mesmo teste (CLAUDE.md §18).
"""

from __future__ import annotations

import pytest

from backend.services import smart_approval as sa


@pytest.fixture(autouse=True)
def _runtime_settings_isolado(tmp_path, monkeypatch):
    """Evita que os testes leiam/gravem no `app_settings` real do usuário."""
    from backend.workspace.runtime_settings import RuntimeSettings

    isolado = RuntimeSettings(tmp_path / "rt.db")
    monkeypatch.setattr(sa, "_runtime_settings", lambda: isolado)
    return isolado


class TestAssinatura:
    def test_hash_cobre_todos_os_argumentos(self):
        # Happy: JSON canônico produz o mesmo hash.
        a = sa._signature("terminal", {"command": "git status"})
        b = sa._signature("terminal", {"command": "git status"})
        assert a == b

        # Erro/borda: qualquer argumento alterado produz outro hash.
        c = sa._signature("terminal", {"command": "git status", "cwd": "other"})
        assert a != c


class TestAllowlist:
    def test_grant_e_de_uso_unico_e_limitado_a_workspace_run_tool_args(self):
        assert sa.get_allowlist("ws1") == []

        args = {"command": "git status"}
        grants = sa.add_to_allowlist("ws1", "terminal", args, run_id="run-1")
        assert len(grants) == 1
        assert sa.consume_run_grant("ws2", "run-1", "terminal", args) is None
        assert sa.consume_run_grant("ws1", "run-2", "terminal", args) is None
        assert sa.consume_run_grant("ws1", "run-1", "terminal_tool", args) is None
        assert (
            sa.consume_run_grant("ws1", "run-1", "terminal", {"command": "git push"})
            is None
        )
        assert sa.consume_run_grant("ws1", "run-1", "terminal", args) == grants[0]
        assert sa.consume_run_grant("ws1", "run-1", "terminal", args) is None
        assert sa.get_allowlist("ws1") == []

    def test_workspace_vazio_e_recusado_na_escrita(self):
        """Erro/borda: allowlist é por workspace — sem id não há onde
        persistir, e gravar sob chave vazia vazaria entre workspaces."""
        with pytest.raises(ValueError, match="workspace_id"):
            sa.add_to_allowlist("", "terminal", {"command": "git status"}, run_id="run")

    def test_run_vazio_e_ferramenta_irreversivel_sao_recusados(self):
        with pytest.raises(ValueError, match="run_id"):
            sa.add_to_allowlist("ws", "terminal", {}, run_id=" ")
        with pytest.raises(ValueError, match="não aceita"):
            sa.add_to_allowlist("ws", "git_push", {}, run_id="run")

    def test_workspace_desconhecido_na_leitura_devolve_vazio_sem_lancar(self):
        assert sa.get_allowlist("ws-nunca-existiu") == []
        assert sa.is_allowlisted("ws-nunca-existiu", "terminal", {}) is False

    def test_allowlists_de_workspaces_diferentes_nao_se_misturam(self):
        args = {"command": "git status"}
        sa.add_to_allowlist("ws-a", "terminal", args, run_id="run-a")

        assert sa.consume_run_grant("ws-a", "run-a", "terminal", args) is not None
        assert sa.consume_run_grant("ws-b", "run-a", "terminal", args) is None

    def test_adicionar_duplicata_nao_duplica(self):
        sa.add_to_allowlist("ws1", "terminal", {"command": "git status"}, run_id="run")
        sinais = sa.add_to_allowlist(
            "ws1", "terminal", {"command": "git status"}, run_id="run"
        )
        assert len(sinais) == 1

    def test_concessao_expirada_nao_e_consumida(self):
        sa.add_to_allowlist("ws", "terminal", {"command": "ls"}, run_id="run")
        sa._runtime_settings().update_list(
            sa._allowlist_key("ws"),
            lambda items: [
                {**item, "expires_at": "2000-01-01T00:00:00+00:00"}
                for item in items
                if isinstance(item, dict)
            ],
        )
        assert sa.consume_run_grant("ws", "run", "terminal", {"command": "ls"}) is None

    def test_concessao_com_expiracao_sem_fuso_ou_invalida_e_descartada(self):
        sa.add_to_allowlist("ws", "terminal", {"command": "ls"}, run_id="run")
        sa._runtime_settings().update_list(
            sa._allowlist_key("ws"),
            lambda items: [
                {**item, "expires_at": "2099-01-01T00:00:00"}
                for item in items
                if isinstance(item, dict)
            ],
        )

        # Datas sem fuso são inválidas para comparar com UTC e devem falhar fechadas.
        assert sa.get_allowlist("ws") == []
        assert sa.consume_run_grant("ws", "run", "terminal", {"command": "ls"}) is None


class TestEvaluateCommand:
    @pytest.mark.asyncio
    async def test_concessao_nao_vira_anotacao_visual_de_avaliador(self):
        chamou = {"llm": False}

        async def _nunca(*_a, **_k):
            chamou["llm"] = True
            return True

        sa.add_to_allowlist(
            "ws1", "terminal", {"command": "git status"}, run_id="run-1"
        )

        resultado = await sa.evaluate_command(
            "terminal",
            {"command": "git status"},
            workspace_id="ws1",
            ask_llm=_nunca,
        )

        assert resultado is True
        assert chamou["llm"] is True

    @pytest.mark.asyncio
    async def test_nao_allowlisted_consulta_o_avaliador_auxiliar(self):
        async def _diz_seguro(_tool_name, _args):
            return True

        resultado = await sa.evaluate_command(
            "terminal",
            {"command": "ls"},
            workspace_id="ws1",
            ask_llm=_diz_seguro,
        )

        assert resultado is True

        async def _diz_revisar(_tool_name, _args):
            return False

        recusado = await sa.evaluate_command(
            "terminal",
            {"command": "rm -rf /"},
            workspace_id="ws1",
            ask_llm=_diz_revisar,
        )
        assert recusado is False

    @pytest.mark.asyncio
    async def test_falha_do_avaliador_degrada_pra_sem_pre_aprovacao(self):
        """Regra 11: falha na camada acessória nunca impede o HITL normal —
        `evaluate_command` nunca lança, e o padrão seguro é `False`
        (continua pedindo aprovação sem o atalho visual)."""

        async def _explode(_tool_name, _args):
            raise RuntimeError("provider fora do ar")

        resultado = await sa.evaluate_command(
            "terminal",
            {"command": "git status"},
            workspace_id="ws1",
            ask_llm=_explode,
        )

        assert resultado is False

    @pytest.mark.asyncio
    async def test_anotacao_do_avaliador_nao_decide_o_gate(self):
        """O sinal do avaliador nunca é importado pelo predicado do HITL."""
        import inspect

        from backend.engine import hitl

        # A dependência é de fora pra dentro (adapters chama smart_approval),
        # nunca o contrário — hitl não conhece este módulo.
        fonte_hitl = inspect.getsource(hitl)
        assert "smart_approval" not in fonte_hitl
