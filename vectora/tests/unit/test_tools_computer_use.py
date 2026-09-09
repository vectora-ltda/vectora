"""Tool `computer_use` — controle de mouse/teclado da tela do desktop.

A tool de maior risco do produto: age fora do sandbox de arquivo/terminal,
em cima da máquina de verdade do usuário. Dois invariantes travados aqui,
os dois testados nos dois sentidos:

- **Sempre pausa para aprovação**, mesmo em `permission_mode="bypass"` — é a
  única tool com essa exceção (as demais respeitam o modo da sessão).
- **Desligada por padrão**: só existe quando o workspace tem
  `[computer_use] enabled = true` explícito no `vectora.toml`. Sem a seção,
  a tool recusa antes de tocar no mouse/teclado — fail-closed.

Cada caminho feliz tem o par de erro/borda no mesmo teste.

Tool nativa (`@vtool`) — chamada como função async direta com
`ctx: ToolContext`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.desktop_windows import DesktopWindowRegistry, WindowInfo
from backend.tools import computer_use as cu
from backend.tools.context import ToolContext


def _ctx(workspace_id: str = "ws1") -> ToolContext:
    return ToolContext(workspace_id=workspace_id, thread_id="t-cu")


class TestOptIn:
    async def test_sem_secao_computer_use_recusa_sem_tocar_na_tela(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Erro/borda: workspace sem `[computer_use]` — fail-closed, a tool
        nem chega perto do mouse/teclado."""
        chamou = {"screenshot": False}
        monkeypatch.setattr(cu, "_computer_use_enabled", lambda _workspace_id: False)
        monkeypatch.setattr(
            cu, "_take_screenshot_sync", lambda: chamou.__setitem__("screenshot", True)
        )

        saida = json.loads(await cu.computer_use(action="screenshot", ctx=_ctx()))

        assert "error" in saida
        assert "computer_use" in saida["error"]
        assert chamou["screenshot"] is False

    async def test_com_secao_habilitada_a_tool_executa(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cu, "_computer_use_enabled", lambda _workspace_id: True)
        monkeypatch.setattr(cu, "_take_screenshot_sync", lambda *_args: b"\x89PNG\r\n")

        saida = json.loads(await cu.computer_use(action="screenshot", ctx=_ctx()))

        assert saida == {"status": "error", "code": "window_unavailable"}

    def test_le_o_toml_de_verdade_via_load_workspace_config(
        self, tmp_path: Path
    ) -> None:
        """A checagem real (não mockada) lê `[computer_use]` do
        `vectora.toml` do workspace — happy e ausência no mesmo teste."""
        (tmp_path / "vectora.toml").write_text(
            "[computer_use]\nenabled = true\n", encoding="utf-8"
        )
        assert cu._computer_use_enabled_for_cwd(str(tmp_path)) is True

        # Erro/borda: seção ausente é `False`, não um "assume desligado"
        # implícito que dependeria do caller lembrar de checar `None`.
        outro = tmp_path / "sem-secao"
        outro.mkdir()
        (outro / "vectora.toml").write_text(
            "[workspace]\nname = 'x'\n", encoding="utf-8"
        )
        assert cu._computer_use_enabled_for_cwd(str(outro)) is False

        # Sem vectora.toml nenhum também é False, não exceção.
        vazio = tmp_path / "sem-toml"
        vazio.mkdir()
        assert cu._computer_use_enabled_for_cwd(str(vazio)) is False


class TestAcoes:
    @pytest.fixture(autouse=True)
    def _habilitado(self, monkeypatch):
        monkeypatch.setattr(cu, "_computer_use_enabled", lambda _workspace_id: True)
        info = WindowInfo("window-1", "Fixture", 0, 0, 1200, 800, True)
        selection = SimpleNamespace(
            window_id=info.window_id, native=SimpleNamespace(_hWnd=1), info=info
        )
        monkeypatch.setattr(
            cu.desktop_window_registry, "selected", lambda **_: selection
        )
        monkeypatch.setattr(
            cu.desktop_window_registry, "require_focus", lambda _selection: info
        )

    async def test_screenshot_devolve_path_do_arquivo_gerado(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(cu, "window_media_dir", lambda _thread_id: tmp_path)
        monkeypatch.setattr(
            cu, "_take_screenshot_sync", lambda *_args: b"\x89PNG\r\nfake"
        )

        saida = json.loads(await cu.computer_use(action="screenshot", ctx=_ctx()))

        assert saida["path"].endswith(".png")
        assert not Path(saida["path"]).is_absolute()

    async def test_click_exige_coordenadas_e_falha_de_biblioteca_vira_erro_tipado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = []
        monkeypatch.setattr(
            cu, "_click_sync", lambda x, y, *_args: chamadas.append((x, y))
        )

        saida = json.loads(
            await cu.computer_use(action="click", x=100, y=200, ctx=_ctx())
        )
        assert saida["action"] == "click"
        assert chamadas == [(100, 200)]

        # Erro/borda: sem x/y não há onde clicar — recusa antes de chamar a
        # biblioteca, que aceitaria None e clicaria na posição atual do
        # mouse sem o usuário ter pedido isso.
        chamadas.clear()
        sem_coords = json.loads(await cu.computer_use(action="click", ctx=_ctx()))
        assert "error" in sem_coords
        assert chamadas == []

    async def test_type_text_digita_e_texto_vazio_e_recusado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        digitado = []
        monkeypatch.setattr(
            cu, "_type_text_sync", lambda text, *_args: digitado.append(text)
        )

        saida = json.loads(
            await cu.computer_use(action="type_text", text="oi", ctx=_ctx())
        )
        assert saida["action"] == "type_text"
        assert digitado == ["oi"]

        digitado.clear()
        vazio = json.loads(
            await cu.computer_use(action="type_text", text="", ctx=_ctx())
        )
        assert "error" in vazio
        assert digitado == []

    async def test_falha_da_biblioteca_de_automacao_nunca_propaga(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regra 11: tool defensiva — exceção vira observação pro LLM, o
        grafo não cai."""

        def _explode(_x, _y):
            raise RuntimeError("X11 display não encontrado")

        monkeypatch.setattr(cu, "_click_sync", _explode)

        saida = json.loads(await cu.computer_use(action="click", x=1, y=1, ctx=_ctx()))
        assert saida == {"status": "error", "code": "platform_failure"}

    async def test_acao_desconhecida_e_recusada(self):
        saida = json.loads(await cu.computer_use(action="explodir_tudo", ctx=_ctx()))
        assert "error" in saida


class TestAprovacaoSempreObrigatoria:
    def test_computer_use_pausa_mesmo_em_bypass(self):
        """Invariante de maior risco do plano: nenhuma tool além desta
        ignora o `permission_mode` da sessão — `bypass`/`auto` normalmente
        nunca pausam, mas `computer_use` sempre pausa."""
        from backend.engine.hitl import _mode_should_interrupt

        for modo in ("bypass", "auto", "ask", "accept_edits", "plan"):
            assert _mode_should_interrupt(modo, "computer_use", []) is True
            assert _mode_should_interrupt(modo, "select_desktop_window", []) is True
            assert _mode_should_interrupt(modo, "focus_desktop_window", []) is True

    def test_esta_em_require_approval(self):
        from backend.engine.hitl import REQUIRE_APPROVAL

        assert "computer_use" in REQUIRE_APPROVAL
        assert "select_desktop_window" in REQUIRE_APPROVAL
        assert "focus_desktop_window" in REQUIRE_APPROVAL

    def test_registrada_em_all_tools(self):
        from backend.nodes.tools import ALL_TOOLS

        assert "computer_use" in {t.name for t in ALL_TOOLS}


class TestJanelaSelecionada:
    async def test_listagem_e_selecao_sao_limitadas_ao_contexto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        info = WindowInfo("w1", "Editor", 10, 20, 800, 600, True)
        monkeypatch.setattr(cu, "_computer_use_enabled", lambda _workspace_id: True)
        monkeypatch.setattr(
            cu.desktop_window_registry, "list_windows", lambda **_: [info]
        )
        monkeypatch.setattr(cu.desktop_window_registry, "select", lambda **_: info)
        ctx = _ctx()

        listed = json.loads(await cu.list_desktop_windows(ctx=ctx))
        selected = json.loads(await cu.select_desktop_window(window_id="w1", ctx=ctx))

        assert listed["windows"] == [
            {
                "window_id": "w1",
                "title": "Editor",
                "geometry": {"width": 800, "height": 600},
                "visible": True,
            }
        ]
        assert selected["status"] == "selected"

    async def test_click_fora_da_janela_e_bloqueado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cu, "_computer_use_enabled", lambda _workspace_id: True)
        info = WindowInfo("w1", "Editor", 0, 0, 10, 10, True)
        selection = SimpleNamespace(
            window_id="w1", native=SimpleNamespace(_hWnd=1), info=info
        )
        monkeypatch.setattr(
            cu.desktop_window_registry, "selected", lambda **_: selection
        )
        monkeypatch.setattr(
            cu.desktop_window_registry, "require_focus", lambda _selection: info
        )
        clicked = []
        monkeypatch.setattr(cu, "_click_sync", lambda x, y: clicked.append((x, y)))

        result = json.loads(
            await cu.computer_use(action="click", x=10, y=0, ctx=_ctx())
        )

        assert result["error"]
        assert clicked == []


def test_desktop_registry_invalidate_e_rate_limit_atomico() -> None:
    registry = DesktopWindowRegistry()
    scope = dict(user_id="u", workspace_id="w", thread_id="t")
    results = [registry.allow_action(**scope) for _ in range(35)]
    assert sum(results) == 30
    registry.invalidate("t")
    assert registry.allow_action(**scope) is True
