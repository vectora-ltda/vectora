"""NATS sidecar — spawn/readiness/shutdown do backend Python.

Mesmo padrão de sidecar que o Electron já usa pro backend Python
(resolve o binário, escolhe porta livre, lê stdout até o sinal de "pronto",
encerra limpo) — aqui é o próprio backend que sobe o nats-server, um nível
abaixo. Sem o binário disponível (dev sem instalação local, CI), degrada
pra None sem lançar — get_mq()/get_kv() caem pro fallback em memória.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduling import nats_sidecar


@pytest.fixture(autouse=True)
def _reset_sidecar_state(tmp_path, monkeypatch):
    # `Path.home()` isolado por padrão em TODO teste deste arquivo — sem
    # isso, qualquer teste que não pense em PID file acaba lendo/escrevendo
    # o ~/.vectora/nats real do dev (um pid file real deixado por uma
    # execução anterior faria `ensure_nats_sidecar()` chamar `_pid_is_alive`
    # contra um PID de verdade, tornando o teste dependente do disco do
    # host). Testes de `TestOrphanPidFile` pedem `tmp_path` de novo — é a
    # MESMA instância (cache por teste do pytest), então ficam coerentes.
    monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)

    # Sem isso, em máquina Windows de verdade (como esta), qualquer teste
    # que sobe o sidecar com sucesso chamaria a Job Object real do Windows
    # contra o `proc.pid` do fake — mesmo com um int válido (não um
    # MagicMock, já corrigido acima), ainda seria uma chamada real de
    # `ctypes` contra um PID arbitrário que pode coincidir com um processo
    # de verdade rodando na máquina. `TestJobObjectIntegration` sobrescreve
    # este patch localmente pra testar o caminho de verdade.
    monkeypatch.setattr(
        "backend.services.win_job_object.create_job_object", lambda: None
    )

    nats_sidecar._proc = None
    nats_sidecar._url = None
    nats_sidecar._log_task = None
    nats_sidecar._job_handle = None
    yield
    if nats_sidecar._log_task is not None:
        nats_sidecar._log_task.cancel()
    nats_sidecar._proc = None
    nats_sidecar._url = None
    nats_sidecar._log_task = None
    nats_sidecar._job_handle = None


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_returns_none_when_binary_not_found():
    with patch.object(nats_sidecar, "_resolve_binary", return_value=None):
        url = await nats_sidecar.ensure_nats_sidecar()

    assert url is None
    assert nats_sidecar._proc is None


def test_resolve_binary_honra_override_env(tmp_path, monkeypatch):
    """VECTORA_NATS_BINARY (apontado pelo Electron pro binário empacotado) tem
    prioridade sobre PATH/resource, e só vale se o arquivo existir."""
    fake = tmp_path / "nats-server"
    fake.write_text("")  # arquivo existe

    monkeypatch.setenv("VECTORA_NATS_BINARY", str(fake))
    # Mesmo com um nats-server no PATH, o override vence.
    monkeypatch.setattr(nats_sidecar.shutil, "which", lambda _n: "/usr/bin/nats-server")
    assert nats_sidecar._resolve_binary() == str(fake)

    # Par de erro: override apontando pra arquivo inexistente é ignorado (cai
    # no PATH), nunca devolve um caminho quebrado.
    monkeypatch.setenv("VECTORA_NATS_BINARY", str(tmp_path / "nao-existe"))
    assert nats_sidecar._resolve_binary() == "/usr/bin/nats-server"


def test_resolve_binary_usa_bundle_pyinstaller_antes_do_path(tmp_path, monkeypatch):
    """No binário congelado (build-hybrid.py empacota vectora/resources/
    nats-server via PyInstaller --add-binary em ``nats/``), sys._MEIPASS deve
    vencer o PATH — senão um nats-server desalinhado instalado na máquina do
    usuário (versão diferente da testada) seria usado em produção."""
    monkeypatch.delenv("VECTORA_NATS_BINARY", raising=False)
    nats_dir = tmp_path / "nats"
    nats_dir.mkdir()
    bundled = nats_dir / nats_sidecar._exe_name()
    bundled.write_text("")

    monkeypatch.setattr(nats_sidecar.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(nats_sidecar.shutil, "which", lambda _n: "/usr/bin/nats-server")

    assert nats_sidecar._resolve_binary() == str(bundled)


def test_resolve_binary_meipass_sem_arquivo_cai_no_path(tmp_path, monkeypatch):
    """Borda: _MEIPASS setado mas sem nats/nats-server dentro (build sem
    `scons nats` antes do PyInstaller) — não trava, degrada pro PATH."""
    monkeypatch.delenv("VECTORA_NATS_BINARY", raising=False)
    monkeypatch.setattr(nats_sidecar.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(nats_sidecar.shutil, "which", lambda _n: "/usr/bin/nats-server")

    assert nats_sidecar._resolve_binary() == "/usr/bin/nats-server"


def test_resolve_binary_usa_nuitka_compiled_containing_dir(tmp_path, monkeypatch):
    """Onefile Nuitka expõe sys.__compiled__.containing_dir — mesma resolução
    usada por server.py::_chat_static_root para o bundle do frontend."""
    monkeypatch.delenv("VECTORA_NATS_BINARY", raising=False)
    monkeypatch.delattr(nats_sidecar.sys, "_MEIPASS", raising=False)
    nats_dir = tmp_path / "nats"
    nats_dir.mkdir()
    bundled = nats_dir / nats_sidecar._exe_name()
    bundled.write_text("")

    fake_compiled = type("FakeCompiled", (), {"containing_dir": str(tmp_path)})()
    monkeypatch.setattr(nats_sidecar.sys, "__compiled__", fake_compiled, raising=False)
    monkeypatch.setattr(nats_sidecar.shutil, "which", lambda _n: None)

    assert nats_sidecar._resolve_binary() == str(bundled)


def _fake_ready_proc(ready_line: bytes = b"Server is ready\n") -> MagicMock:
    proc = MagicMock()
    # pid precisa ser um int real, não o MagicMock auto-gerado: nesta
    # máquina (Windows de verdade) `_assign_to_job_object_best_effort`
    # passa `proc.pid` pra `ctypes` de verdade — um MagicMock nesse lugar
    # trava o interpretador (a marshaling do ctypes tenta coagir o mock pra
    # C int via atributos que o próprio MagicMock intercepta, recursão
    # infinita → stack overflow nativo, não capturável por try/except).
    proc.pid = 4242
    # Depois da linha de "ready", EOF — sem isso a task de background
    # `pipe_to_logger` (criada após o handshake) ficaria lendo a mesma
    # linha pra sempre, um loop infinito consumindo CPU no teste.
    proc.stdout.readline = AsyncMock(side_effect=[ready_line, b""])
    proc.returncode = None
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_spawns_and_returns_url_when_ready():
    fake_proc = _fake_ready_proc()

    with (
        patch.object(
            nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
        ),
        patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
        ) as spawn_mock,
    ):
        url = await nats_sidecar.ensure_nats_sidecar()

    assert url is not None
    assert url.startswith("nats://127.0.0.1:")
    assert nats_sidecar._proc is fake_proc
    assert spawn_mock.await_args is not None
    if sys.platform == "win32":
        assert spawn_mock.await_args.kwargs["creationflags"] > 0
    else:
        assert spawn_mock.await_args.kwargs["start_new_session"] is True


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_kills_process_and_returns_none_when_not_ready():
    """Edge — processo sobe mas nunca emite "Server is ready" (porta ocupada etc.)."""
    never_ready_proc = MagicMock()
    never_ready_proc.stdout.readline = AsyncMock(return_value=b"")  # EOF imediato
    never_ready_proc.kill = MagicMock()

    with (
        patch.object(
            nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
        ),
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=never_ready_proc),
        ),
    ):
        url = await nats_sidecar.ensure_nats_sidecar()

    assert url is None
    never_ready_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_reuses_running_process():
    fake_proc = _fake_ready_proc()
    nats_sidecar._proc = fake_proc
    nats_sidecar._url = "nats://127.0.0.1:4222"

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn_mock:
        url = await nats_sidecar.ensure_nats_sidecar()

    assert url == "nats://127.0.0.1:4222"
    spawn_mock.assert_not_called()


@pytest.mark.asyncio
async def test_stop_nats_sidecar_terminates_and_clears_state():
    fake_proc = MagicMock()
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    nats_sidecar._proc = fake_proc
    nats_sidecar._url = "nats://127.0.0.1:4222"

    await nats_sidecar.stop_nats_sidecar()

    fake_proc.terminate.assert_called_once()
    assert nats_sidecar._proc is None
    assert nats_sidecar.current_url() is None


@pytest.mark.asyncio
async def test_stop_nats_sidecar_without_running_process_is_noop():
    await nats_sidecar.stop_nats_sidecar()
    assert nats_sidecar._proc is None


class TestOrphanPidFile:
    """Um PID file cross-processo rastreia sidecars órfãos deixados por uma
    sessão anterior que morreu sem passar pelo shutdown gracioso: antes de
    spawnar um novo sidecar, `ensure_nats_sidecar()` mata qualquer PID vivo
    registrado no arquivo.

    O rastreamento é uma LISTA de PIDs (não um único), varrida best-effort
    (cada PID isolado por try/except) — assim uma falha isolada ao matar um
    PID não derruba o rastreamento dos demais."""

    def test_pid_file_path_fica_dentro_do_store_dir(self, tmp_path):
        assert nats_sidecar._pid_file_path(tmp_path) == tmp_path / "sidecar.pid"

    def test_write_e_read_pid_list_roundtrip(self, tmp_path):
        nats_sidecar._write_pid_list(tmp_path, [4242, 5353])
        assert nats_sidecar._read_stale_pids(tmp_path) == [4242, 5353]

    def test_read_stale_pids_sem_arquivo_retorna_lista_vazia(self, tmp_path):
        assert nats_sidecar._read_stale_pids(tmp_path) == []

    def test_read_stale_pids_arquivo_corrompido_retorna_lista_vazia(self, tmp_path):
        # Erro/borda: JSON inválido ou campo ausente nunca derruba o caller —
        # degrada pra "nenhum pid conhecido" (fail-safe, não fail-open pra
        # matar processo errado).
        (tmp_path / "sidecar.pid").write_text("{nao e json valido", encoding="utf-8")
        assert nats_sidecar._read_stale_pids(tmp_path) == []

    def test_read_stale_pids_aceita_formato_legado_pid_unico(self, tmp_path):
        # Compatibilidade com o formato D3 ({"pid": N}) — um pid file antigo
        # já em disco no momento do upgrade não deve ser ignorado.
        (tmp_path / "sidecar.pid").write_text(
            json.dumps({"pid": 7777}), encoding="utf-8"
        )
        assert nats_sidecar._read_stale_pids(tmp_path) == [7777]

    def test_write_pid_list_respeita_o_cap(self, tmp_path):
        many_pids = list(range(1, 20))
        nats_sidecar._write_pid_list(tmp_path, many_pids)
        assert (
            len(nats_sidecar._read_stale_pids(tmp_path)) == nats_sidecar._PID_LIST_CAP
        )

    def test_clear_pid_file_remove_e_e_idempotente(self, tmp_path):
        nats_sidecar._write_pid_list(tmp_path, [4242])
        nats_sidecar._clear_pid_file(tmp_path)
        assert not (tmp_path / "sidecar.pid").is_file()
        nats_sidecar._clear_pid_file(tmp_path)  # segunda chamada não lança

    def test_remove_pid_preserva_os_demais(self, tmp_path):
        nats_sidecar._write_pid_list(tmp_path, [111, 222, 333])
        nats_sidecar._remove_pid(tmp_path, 222)
        assert nats_sidecar._read_stale_pids(tmp_path) == [111, 333]

    def test_remove_pid_ultimo_da_lista_limpa_o_arquivo(self, tmp_path):
        nats_sidecar._write_pid_list(tmp_path, [4242])
        nats_sidecar._remove_pid(tmp_path, 4242)
        assert not (tmp_path / "sidecar.pid").is_file()

    @pytest.mark.asyncio
    async def test_ensure_nats_sidecar_mata_orfao_registrado_antes_de_spawnar(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [9999])

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 12345
        kill_mock = MagicMock()

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch.object(nats_sidecar, "_pid_is_alive", return_value=True),
            patch.object(nats_sidecar, "_kill_pid", kill_mock),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        kill_mock.assert_called_once_with(9999)
        assert url is not None
        # Órfão morto e substituído — o pid file agora aponta pro processo novo.
        assert nats_sidecar._read_stale_pids(store_dir) == [12345]

    @pytest.mark.asyncio
    async def test_ensure_nats_sidecar_ignora_pid_morto_sem_tentar_matar(
        self, tmp_path, monkeypatch
    ):
        # Par de erro/borda: pid file existe mas o processo já morreu sozinho
        # (ex.: crash) — não deve chamar kill à toa, só limpar e seguir.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [9999])

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 12345
        kill_mock = MagicMock()

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch.object(nats_sidecar, "_pid_is_alive", return_value=False),
            patch.object(nats_sidecar, "_kill_pid", kill_mock),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
        ):
            await nats_sidecar.ensure_nats_sidecar()

        kill_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_nats_sidecar_varre_multiplos_orfaos_isolando_falhas(
        self, tmp_path, monkeypatch
    ):
        # Lista com 3 órfãos, um deles falha ao matar — os outros dois ainda
        # são processados, e a subida do sidecar novo não é afetada.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [111, 222, 333])

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 999
        kill_mock = MagicMock(side_effect=[None, RuntimeError("taskkill falhou"), None])

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch.object(nats_sidecar, "_pid_is_alive", return_value=True),
            patch.object(nats_sidecar, "_kill_pid", kill_mock),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        assert kill_mock.call_count == 3
        assert url is not None
        assert nats_sidecar._read_stale_pids(store_dir) == [999]

    @pytest.mark.asyncio
    async def test_ensure_nats_sidecar_pid_is_alive_lancando_nao_aborta_a_subida(
        self, tmp_path, monkeypatch
    ):
        # Erro/borda: uma exceção em `_pid_is_alive` (ex. stdout=None em
        # `_pid_is_alive_win32`) nunca deve impedir o sidecar novo de subir.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [9999])

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 12345

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch.object(
                nats_sidecar,
                "_pid_is_alive",
                side_effect=TypeError("argument of type 'NoneType' is not iterable"),
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        assert url is not None
        assert nats_sidecar._read_stale_pids(store_dir) == [12345]

    @pytest.mark.asyncio
    async def test_stop_nats_sidecar_limpa_o_pid_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [4242])

        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=None)
        nats_sidecar._proc = fake_proc
        nats_sidecar._url = "nats://127.0.0.1:4222"

        await nats_sidecar.stop_nats_sidecar()

        assert nats_sidecar._read_stale_pids(store_dir) == []

    @pytest.mark.asyncio
    async def test_stop_nats_sidecar_preserva_orfaos_nao_relacionados(
        self, tmp_path, monkeypatch
    ):
        # Erro/borda: se por algum motivo a lista tem outros PIDs além do
        # processo atual, stop_nats_sidecar não deve apagá-los — só remove
        # o PID do processo que ele mesmo está encerrando.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        store_dir = tmp_path / "nats"
        store_dir.mkdir(parents=True)
        nats_sidecar._write_pid_list(store_dir, [4242, 8888])

        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock(return_value=None)
        nats_sidecar._proc = fake_proc
        nats_sidecar._url = "nats://127.0.0.1:4222"

        await nats_sidecar.stop_nats_sidecar()

        assert nats_sidecar._read_stale_pids(store_dir) == [8888]


class TestJobObjectIntegration:
    """Defesa em profundidade contra SIGKILL/"Finalizar tarefa" — associa o
    sidecar recém-criado a uma Windows Job Object (ver `backend/services/
    win_job_object.py`). Windows-only, best-effort: nunca impede o sidecar
    de subir mesmo se a associação falhar."""

    @pytest.mark.asyncio
    async def test_nao_chama_job_object_fora_do_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        monkeypatch.setattr(nats_sidecar.sys, "platform", "linux")

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 111

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
            patch("backend.services.win_job_object.create_job_object") as create_mock,
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        assert url is not None
        create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_associa_processo_a_job_object_no_windows(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        monkeypatch.setattr(nats_sidecar.sys, "platform", "win32")

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 222

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
            patch(
                "backend.services.win_job_object.create_job_object", return_value=99
            ) as create_mock,
            patch(
                "backend.services.win_job_object.assign_process_to_job",
                return_value=True,
            ) as assign_mock,
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        assert url is not None
        create_mock.assert_called_once()
        assign_mock.assert_called_once_with(99, 222)
        assert nats_sidecar._job_handle == 99

    @pytest.mark.asyncio
    async def test_job_object_falhando_nao_impede_sidecar_de_subir(
        self, tmp_path, monkeypatch
    ):
        # Erro/borda central: exceção na criação da Job Object nunca deve
        # aparecer pro chamador de ensure_nats_sidecar.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        monkeypatch.setattr(nats_sidecar.sys, "platform", "win32")

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 333

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
            patch(
                "backend.services.win_job_object.create_job_object",
                side_effect=RuntimeError("boom"),
            ),
        ):
            url = await nats_sidecar.ensure_nats_sidecar()

        assert url is not None

    @pytest.mark.asyncio
    async def test_job_handle_reaproveitado_entre_subidas(self, tmp_path, monkeypatch):
        # Não recria a Job Object a cada subida — só na primeira vez.
        monkeypatch.setattr(nats_sidecar.settings, "vectora_home", tmp_path)
        monkeypatch.setattr(nats_sidecar.sys, "platform", "win32")
        nats_sidecar._job_handle = 555

        fake_proc = _fake_ready_proc()
        fake_proc.pid = 444

        with (
            patch.object(
                nats_sidecar, "_resolve_binary", return_value="/usr/bin/nats-server"
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)
            ),
            patch("backend.services.win_job_object.create_job_object") as create_mock,
            patch(
                "backend.services.win_job_object.assign_process_to_job",
                return_value=True,
            ) as assign_mock,
        ):
            await nats_sidecar.ensure_nats_sidecar()

        create_mock.assert_not_called()
        assign_mock.assert_called_once_with(555, 444)
