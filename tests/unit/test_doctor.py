"""``vectora doctor`` — encontra e limpa sidecars nats-server órfãos por nome
de imagem (não só os PIDs conhecidos pelo pid file de nats_sidecar.py)."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.cli import doctor


def _run(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


class TestFindNatsServerPidsWin32:
    def test_parsing_feliz_da_saida_do_tasklist(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        tasklist_output = (
            "nats-server.exe             12345 Console                    1      6.676 K\n"
            "nats-server.exe              9999 Console                    1      6.548 K\n"
        )
        with (
            patch.object(
                doctor.shutil, "which", return_value="C:\\Windows\\tasklist.exe"
            ),
            patch.object(doctor.subprocess, "run", return_value=_run(tasklist_output)),
        ):
            pids = doctor.find_nats_server_pids()

        assert pids == [12345, 9999]

    def test_tasklist_ausente_retorna_lista_vazia(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        with patch.object(doctor.shutil, "which", return_value=None):
            assert doctor.find_nats_server_pids() == []

    def test_subprocess_lancando_retorna_lista_vazia(self, monkeypatch):
        # Erro/borda: qualquer falha do subprocess (timeout, permissão)
        # nunca deve propagar — degrada pra "nada encontrado".
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        with (
            patch.object(doctor.shutil, "which", return_value="tasklist"),
            patch.object(doctor.subprocess, "run", side_effect=OSError("boom")),
        ):
            assert doctor.find_nats_server_pids() == []

    def test_saida_sem_processos_retorna_lista_vazia(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        with (
            patch.object(doctor.shutil, "which", return_value="tasklist"),
            patch.object(
                doctor.subprocess,
                "run",
                return_value=_run("INFO: No tasks are running...\n"),
            ),
        ):
            assert doctor.find_nats_server_pids() == []


class TestFindNatsServerPidsPosix:
    def test_parsing_feliz_da_saida_do_pgrep(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        with (
            patch.object(doctor.shutil, "which", return_value="/usr/bin/pgrep"),
            patch.object(doctor.subprocess, "run", return_value=_run("111\n222\n")),
        ):
            pids = doctor.find_nats_server_pids()

        assert pids == [111, 222]

    def test_pgrep_ausente_retorna_lista_vazia(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        with patch.object(doctor.shutil, "which", return_value=None):
            assert doctor.find_nats_server_pids() == []


class TestRunDoctor:
    @pytest.fixture(autouse=True)
    def _no_sandbox_check(self):
        # run_doctor() também dispara _report_sandbox_status (checa WSL2 no
        # Windows) — testes desta classe cobrem só a lógica de nats-server,
        # então neutraliza pra não disparar `wsl.exe` de verdade (não-hermético).
        with patch.object(doctor, "_report_sandbox_status", AsyncMock()):
            yield

    def test_sem_orfaos_nao_mata_nada(self, capsys):
        with (
            patch.object(doctor, "find_nats_server_pids", return_value=[]),
            patch("backend.scheduling.nats_sidecar.kill_orphan_pid") as kill_mock,
        ):
            doctor.run_doctor(argparse.Namespace(yes=False))

        kill_mock.assert_not_called()

    def test_com_yes_mata_sem_perguntar(self):
        with (
            patch.object(doctor, "find_nats_server_pids", return_value=[111, 222]),
            patch("backend.scheduling.nats_sidecar.kill_orphan_pid") as kill_mock,
        ):
            doctor.run_doctor(argparse.Namespace(yes=True))

        assert kill_mock.call_count == 2
        kill_mock.assert_any_call(111)
        kill_mock.assert_any_call(222)

    def test_reporta_status_do_sandbox_antes_de_procurar_orfaos(self):
        # _report_sandbox_status roda mesmo sem nenhum órfão nats-server —
        # é um diagnóstico independente, não condicionado ao resto.
        with (
            patch.object(doctor, "find_nats_server_pids", return_value=[]),
            patch.object(doctor, "_report_sandbox_status", AsyncMock()) as sandbox_mock,
        ):
            doctor.run_doctor(argparse.Namespace(yes=False))

        sandbox_mock.assert_awaited_once()


class TestReportSandboxStatus:
    """No Windows, `_report_sandbox_status` diagnostica o caminho real de
    sandbox (WSL2 — bwrap não roda nativo, Docker não é a resposta certa
    aqui)."""

    @pytest.mark.asyncio
    async def test_no_linux_sem_singularity_nao_imprime_nada(self, monkeypatch):
        # bwrap é nativo aqui — sem singularity/apptainer, nada a diagnosticar.
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        console.print.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_linux_com_singularity_informa_alternativa(self, monkeypatch):
        """Com `singularity` instalado, o doctor reporta o backend alternativo
        pro `bwrap` (útil em ambientes HPC/cluster sem daemon Docker)."""
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setattr(
            doctor.shutil,
            "which",
            lambda name: "/usr/bin/singularity" if name == "singularity" else None,
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "singularity" in printed
        assert 'backend = "singularity"' in printed

    @pytest.mark.asyncio
    async def test_no_linux_com_apptainer_informa_alternativa(self, monkeypatch):
        """`apptainer` (rebrand do Singularity) também é detectado, não só o
        binário `singularity` original."""
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setattr(
            doctor.shutil,
            "which",
            lambda name: "/usr/bin/apptainer" if name == "apptainer" else None,
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "apptainer" in printed
        assert 'backend = "singularity"' in printed

    @pytest.mark.asyncio
    async def test_macos_sem_sandbox_exec_recomenda_alternativas(self, monkeypatch):
        """Sem `sandbox-exec` instalado (interface legada da Apple, pode ter
        sido removida), o doctor não finge suporte ao backend nativo —
        orienta pra docker/ssh/modal."""
        monkeypatch.setattr(doctor.sys, "platform", "darwin")
        monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
        # Erro/borda: se o caminho do macOS caísse no ramo do Windows, isto
        # explodiria — a checagem de WSL2 não existe nesse SO.
        monkeypatch.setattr(
            "backend.sandbox.policy.detect_wsl2",
            AsyncMock(side_effect=AssertionError("WSL2 não se aplica ao macOS")),
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "macOS" in printed
        assert "docker" in printed
        assert "indisponível" in printed

    @pytest.mark.asyncio
    async def test_macos_com_sandbox_exec_recomenda_backend_nativo(self, monkeypatch):
        """Com `sandbox-exec` disponível, o doctor aponta o backend nativo
        `"macos"` (Seatbelt) em vez de só recomendar docker/ssh/modal."""
        monkeypatch.setattr(doctor.sys, "platform", "darwin")
        monkeypatch.setattr(
            doctor.shutil,
            "which",
            lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
        )
        monkeypatch.setattr(
            "backend.sandbox.policy.detect_wsl2",
            AsyncMock(side_effect=AssertionError("WSL2 não se aplica ao macOS")),
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "sandbox-exec disponível" in printed
        assert 'backend = "macos"' in printed

    @pytest.mark.asyncio
    async def test_sem_wsl2_orienta_a_instalar(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr(
            "backend.sandbox.policy.detect_wsl2", AsyncMock(return_value=None)
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "wsl --install" in printed

    @pytest.mark.asyncio
    async def test_com_wsl2_e_bwrap_reporta_tudo_ok(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr(
            "backend.sandbox.policy.detect_wsl2", AsyncMock(return_value="Ubuntu")
        )
        monkeypatch.setattr(
            "backend.sandbox.workspace_jail._bwrap_available_in_distro",
            AsyncMock(return_value=True),
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "Ubuntu" in printed
        assert "bwrap instalado" in printed

    @pytest.mark.asyncio
    async def test_com_wsl2_mas_sem_bwrap_na_distro_orienta_instalar(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "win32")
        monkeypatch.setattr(
            "backend.sandbox.policy.detect_wsl2", AsyncMock(return_value="Ubuntu")
        )
        monkeypatch.setattr(
            "backend.sandbox.workspace_jail._bwrap_available_in_distro",
            AsyncMock(return_value=False),
        )
        console = MagicMock()

        await doctor._report_sandbox_status(console)

        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list)
        assert "apt install bubblewrap" in printed

    def test_sem_yes_recusa_confirmacao_nao_mata(self):
        # Erro/borda: resposta diferente de y/yes/s/sim cancela, sem matar.
        with (
            patch.object(doctor, "find_nats_server_pids", return_value=[111]),
            patch("builtins.input", return_value="n"),
            patch("backend.scheduling.nats_sidecar.kill_orphan_pid") as kill_mock,
        ):
            doctor.run_doctor(argparse.Namespace(yes=False))

        kill_mock.assert_not_called()

    def test_sem_yes_confirma_e_mata(self):
        with (
            patch.object(doctor, "find_nats_server_pids", return_value=[111]),
            patch("builtins.input", return_value="y"),
            patch("backend.scheduling.nats_sidecar.kill_orphan_pid") as kill_mock,
        ):
            doctor.run_doctor(argparse.Namespace(yes=False))

        kill_mock.assert_called_once_with(111)
