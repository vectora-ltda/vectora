"""Electron sidecar — spawn/readiness/shutdown do
Electron de dentro do lifespan do FastAPI.

Mesmo padrão de sidecar que `backend/scheduling/nats_sidecar.py` já usa
pro `nats-server`: resolve o binário, sobe via `asyncio.create_subprocess_exec`
dentro do event loop já rodando, encerra limpo, reusa processo já vivo. Sem
o build de dev do Electron disponível, degrada pra `None` sem lançar — o
backend segue de pé sem janela.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services import electron_sidecar
from backend.services.subprocess_sidecar_utils import LazyLock


@pytest.fixture(autouse=True)
def _reset_sidecar_state(monkeypatch: pytest.MonkeyPatch):
    # Job Object real (Windows) não deve rodar nos testes que não a testam
    # de verdade — `TestJobObjectIntegration` sobrescreve este patch
    # localmente pra testar o caminho de verdade.
    monkeypatch.setattr(
        "backend.services.win_job_object.create_job_object", lambda: None
    )

    electron_sidecar._proc = None
    electron_sidecar._spawn_lock = LazyLock()
    electron_sidecar._log_task = None
    electron_sidecar._watch_task = None
    electron_sidecar._job_handle = None
    yield
    if electron_sidecar._watch_task is not None:
        electron_sidecar._watch_task.cancel()
    electron_sidecar._proc = None
    electron_sidecar._spawn_lock = LazyLock()
    electron_sidecar._log_task = None
    electron_sidecar._watch_task = None
    electron_sidecar._job_handle = None


# ---------------------------------------------------------------------------
# should_spawn_electron — a decisão
# ---------------------------------------------------------------------------


class TestShouldSpawnElectron:
    def test_ja_sob_electron_retorna_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VECTORA_DESKTOP", "1")
        assert electron_sidecar.should_spawn_electron() is False

    def test_headless_retorna_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
        monkeypatch.setenv("VECTORA_HEADLESS", "1")
        assert electron_sidecar.should_spawn_electron() is False

    def test_sem_display_retorna_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
        monkeypatch.delenv("VECTORA_HEADLESS", raising=False)
        with patch("backend.services.electron_sidecar.has_display", return_value=False):
            assert electron_sidecar.should_spawn_electron() is False

    def test_sem_build_de_dev_retorna_false(self, monkeypatch: pytest.MonkeyPatch):
        # Par de erro/edge case: display disponível mas Electron não
        # buildado (scons frontend não rodou) — não trava, só diz não.
        monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
        monkeypatch.delenv("VECTORA_HEADLESS", raising=False)
        with (
            patch("backend.services.electron_sidecar.has_display", return_value=True),
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=None,
            ),
        ):
            assert electron_sidecar.should_spawn_electron() is False

    def test_tudo_disponivel_retorna_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VECTORA_DESKTOP", raising=False)
        monkeypatch.delenv("VECTORA_HEADLESS", raising=False)
        with (
            patch("backend.services.electron_sidecar.has_display", return_value=True),
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
        ):
            assert electron_sidecar.should_spawn_electron() is True


# ---------------------------------------------------------------------------
# ensure_electron_sidecar / stop_electron_sidecar — spawn mockado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_retorna_none_sem_build_resolvido():
    with patch(
        "backend.services.electron_sidecar.resolve_electron_launch",
        return_value=None,
    ):
        proc = await electron_sidecar.ensure_electron_sidecar()

    assert proc is None
    assert electron_sidecar._proc is None


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_spawna_e_guarda_o_processo():
    fake_proc = MagicMock()
    fake_proc.pid = 4242
    fake_proc.returncode = None
    fake_proc.stdout = None  # sem stream real — pipe_to_logger vira no-op

    with (
        patch(
            "backend.services.electron_sidecar.resolve_electron_launch",
            return_value=("electron.exe", ["main.js"]),
        ),
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ) as spawn_mock,
    ):
        proc = await electron_sidecar.ensure_electron_sidecar()

    assert proc is fake_proc
    assert electron_sidecar._proc is fake_proc
    spawn_mock.assert_called_once()
    call_args, call_kwargs = spawn_mock.call_args
    assert call_args == ("electron.exe", "main.js")
    assert call_kwargs["env"]["VECTORA_EXTERNAL_BACKEND"] == "1"
    assert call_kwargs["stdout"] == asyncio.subprocess.PIPE
    assert call_kwargs["stderr"] == asyncio.subprocess.STDOUT
    # dá um tick pro _log_task (pipe_to_logger com stdout=None) rodar e
    # retornar de imediato, sem deixar task/warning pendurado.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_reusa_processo_ja_rodando():
    fake_proc = MagicMock()
    fake_proc.returncode = None
    electron_sidecar._proc = fake_proc

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn_mock:
        proc = await electron_sidecar.ensure_electron_sidecar()

    assert proc is fake_proc
    spawn_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_falha_de_spawn_retorna_none_sem_lancar():
    # Par de erro: binário corrompido/permissão negada não propaga —
    # o backend segue de pé sem janela.
    with (
        patch(
            "backend.services.electron_sidecar.resolve_electron_launch",
            return_value=("electron.exe", ["main.js"]),
        ),
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("binário corrompido")),
        ),
    ):
        proc = await electron_sidecar.ensure_electron_sidecar()

    assert proc is None
    assert electron_sidecar._proc is None


@pytest.mark.asyncio
async def test_stop_electron_sidecar_termina_e_limpa_estado():
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    electron_sidecar._proc = fake_proc

    await electron_sidecar.stop_electron_sidecar()

    fake_proc.terminate.assert_called_once()
    assert electron_sidecar._proc is None


@pytest.mark.asyncio
async def test_stop_electron_sidecar_sem_processo_e_noop():
    await electron_sidecar.stop_electron_sidecar()
    assert electron_sidecar._proc is None


# ---------------------------------------------------------------------------
# S0-7: diagnóstico do "Ctrl+C não fecha a janela do Electron" — cada ramo
# de stop_electron_sidecar() precisa deixar rastro distinguível no log, já
# que o mecanismo de kill em si já foi confirmado funcional (reproduzido ao
# vivo: spawnar um Electron real + terminate_gracefully mata processo e
# renderers, sem órfão). Se a janela ficar presa de novo, o log agora diz
# QUAL dos três estados aconteceu, em vez de nada.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_sem_processo_rastreado_loga_o_motivo(caplog):
    caplog.set_level(logging.INFO, logger="backend.services.electron_sidecar")

    await electron_sidecar.stop_electron_sidecar()

    assert "sem processo rastreado" in caplog.text


@pytest.mark.asyncio
async def test_stop_com_processo_ja_morto_loga_o_code_sem_chamar_terminate(caplog):
    caplog.set_level(logging.INFO, logger="backend.services.electron_sidecar")
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.terminate = MagicMock()
    electron_sidecar._proc = fake_proc

    await electron_sidecar.stop_electron_sidecar()

    fake_proc.terminate.assert_not_called()
    assert "já havia saído" in caplog.text


@pytest.mark.asyncio
async def test_stop_com_processo_vivo_loga_sucesso_apos_terminar(caplog):
    caplog.set_level(logging.INFO, logger="backend.services.electron_sidecar")
    fake_proc = MagicMock()
    fake_proc.pid = 4242
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    electron_sidecar._proc = fake_proc

    await electron_sidecar.stop_electron_sidecar()

    assert "encerrado" in caplog.text
    assert "4242" in caplog.text


# ---------------------------------------------------------------------------
# Job Object (Windows) — defesa contra Electron órfão se o terminal fechar
# ---------------------------------------------------------------------------


class TestJobObjectIntegration:
    """Fechar o terminal que rodou `vectora start` (modo backend-primário)
    não deve deixar o Electron (e o ícone do tray) órfão — ver
    `backend/services/win_job_object.py`. Windows-only, best-effort."""

    @pytest.mark.asyncio
    async def test_nao_associa_job_object_fora_do_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(electron_sidecar.sys, "platform", "linux")
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.stdout = None

        with (
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_proc),
            ),
            patch("backend.services.win_job_object.create_job_object") as create_mock,
        ):
            await electron_sidecar.ensure_electron_sidecar()
            await asyncio.sleep(0)

        create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_associa_processo_a_job_object_no_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(electron_sidecar.sys, "platform", "win32")
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.stdout = None

        with (
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_proc),
            ),
            patch(
                "backend.services.win_job_object.create_job_object",
                return_value=99,
            ) as create_mock,
            patch(
                "backend.services.win_job_object.assign_process_to_job",
                return_value=True,
            ) as assign_mock,
        ):
            await electron_sidecar.ensure_electron_sidecar()
            await asyncio.sleep(0)

        create_mock.assert_called_once()
        assign_mock.assert_called_once_with(99, 4242)
        assert electron_sidecar._job_handle == 99

    @pytest.mark.asyncio
    async def test_job_object_falhando_nao_impede_sidecar_de_subir(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Par de erro/borda: exceção na criação da Job Object nunca deve
        # impedir o Electron de subir — best-effort, só loga.
        monkeypatch.setattr(electron_sidecar.sys, "platform", "win32")
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.stdout = None

        with (
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_proc),
            ),
            patch(
                "backend.services.win_job_object.create_job_object",
                side_effect=RuntimeError("boom"),
            ),
        ):
            proc = await electron_sidecar.ensure_electron_sidecar()
            await asyncio.sleep(0)

        assert proc is fake_proc


# ---------------------------------------------------------------------------
# Watch de saída inesperada — Electron fechado pelo tray derruba o backend
# ---------------------------------------------------------------------------


class TestWatchForUnexpectedExit:
    """Saída normal do Electron permite reconexão; crash encerra o backend."""

    @pytest.mark.asyncio
    async def test_saida_normal_do_electron_preserva_backend_para_reconexao(
        self,
    ):
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.stdout = None
        wait_future: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _wait():
            return await wait_future

        fake_proc.wait = AsyncMock(side_effect=_wait)

        with (
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_proc),
            ),
            patch("backend.services.electron_sidecar.os.kill") as kill_mock,
        ):
            await electron_sidecar.ensure_electron_sidecar()
            await asyncio.sleep(0)

            fake_proc.returncode = 0
            wait_future.set_result(None)
            assert electron_sidecar._watch_task is not None
            await electron_sidecar._watch_task

        kill_mock.assert_not_called()
        assert electron_sidecar._proc is None

    @pytest.mark.asyncio
    async def test_saida_anormal_do_electron_envia_sigterm_ao_backend(self):
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        wait_future: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _wait():
            return await wait_future

        fake_proc.wait = AsyncMock(side_effect=_wait)

        with patch("backend.services.electron_sidecar.os.kill") as kill_mock:
            electron_sidecar._proc = fake_proc
            watcher = asyncio.create_task(
                electron_sidecar._watch_for_unexpected_exit(fake_proc)
            )
            fake_proc.returncode = 1
            wait_future.set_result(None)
            await watcher

        kill_mock.assert_called_once()
        assert kill_mock.call_args.args[1] == electron_sidecar.signal.SIGTERM

    @pytest.mark.asyncio
    async def test_stop_deliberado_nao_dispara_sigterm(self):
        # Par de erro/borda: `stop_electron_sidecar()` (shutdown gracioso
        # do FastAPI) zera `_proc` antes de terminar o processo — o watcher
        # não deve confundir isso com uma saída espontânea do Electron.
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.returncode = None
        fake_proc.stdout = None
        fake_proc.terminate = MagicMock()
        wait_future: asyncio.Future = asyncio.get_event_loop().create_future()
        call_count = 0

        async def _wait():
            # 1ª chamada: o watcher, esperando a saída "espontânea" (nunca
            # resolvida aqui). 2ª chamada: `terminate_gracefully`, depois do
            # watcher já ter sido cancelado — resolve na hora, como um
            # processo que já morreu de verdade.
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await wait_future
            return None

        fake_proc.wait = AsyncMock(side_effect=_wait)

        with (
            patch(
                "backend.services.electron_sidecar.resolve_electron_launch",
                return_value=("electron.exe", ["main.js"]),
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_proc),
            ),
            patch("backend.services.electron_sidecar.os.kill") as kill_mock,
        ):
            await electron_sidecar.ensure_electron_sidecar()
            await asyncio.sleep(0)

            await electron_sidecar.stop_electron_sidecar()
            fake_proc.returncode = 0
            await asyncio.sleep(0)

        kill_mock.assert_not_called()
