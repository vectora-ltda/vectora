"""Tests for backend/services/electron_launcher.py."""

from __future__ import annotations

import http.server
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from backend.services import electron_launcher

_ELECTRON_DEV_UNAVAILABLE_REASON = (
    "build de dev do Electron ausente — rode `pnpm --dir frontend "
    "install && pnpm --dir frontend run electron:build` (ou "
    "`scons frontend`)"
)


def _patch_dirs(monkeypatch, frontend_dir: Path, electron_dir: Path) -> None:
    """node_modules/electron/ vive em frontend_dir (pacote único); dist/main.js
    vive em electron_dir (tsconfig próprio, ver electron_launcher.py)."""
    monkeypatch.setattr(electron_launcher, "_frontend_dir", lambda: frontend_dir)
    monkeypatch.setattr(electron_launcher, "_electron_dir", lambda: electron_dir)


class TestResolveElectronLaunch:
    def test_sem_main_js_retorna_none(self, tmp_path: Path, monkeypatch, caplog):
        _patch_dirs(monkeypatch, tmp_path, tmp_path)
        with caplog.at_level("WARNING"):
            assert electron_launcher.resolve_electron_launch() is None
        assert "ausente" in caplog.text
        assert "electron:build" in caplog.text

    def test_sem_path_txt_retorna_none(self, tmp_path: Path, monkeypatch, caplog):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "main.js").write_text("// main", encoding="utf-8")
        _patch_dirs(monkeypatch, tmp_path, tmp_path)
        with caplog.at_level("WARNING"):
            assert electron_launcher.resolve_electron_launch() is None
        # Erro/borda real encontrado ao vivo: `pnpm install --frozen-lockfile`
        # reporta sucesso mesmo quando o postinstall do pacote `electron`
        # nunca baixou o binário — o log precisa apontar o comando de reparo.
        assert "postinstall" in caplog.text
        assert "pnpm rebuild electron" in caplog.text

    def test_path_txt_vazio_retorna_none(self, tmp_path: Path, monkeypatch, caplog):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "main.js").write_text("// main", encoding="utf-8")
        node_electron = tmp_path / "node_modules" / "electron"
        node_electron.mkdir(parents=True)
        (node_electron / "path.txt").write_text("", encoding="utf-8")
        _patch_dirs(monkeypatch, tmp_path, tmp_path)
        with caplog.at_level("WARNING"):
            assert electron_launcher.resolve_electron_launch() is None
        assert "vazio" in caplog.text

    def test_binario_referenciado_nao_existe_retorna_none(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        # Par de erro: path.txt aponta para um executável que não foi
        # baixado (dist/ do pacote electron incompleto) — nunca lança.
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "main.js").write_text("// main", encoding="utf-8")
        node_electron = tmp_path / "node_modules" / "electron"
        node_electron.mkdir(parents=True)
        (node_electron / "path.txt").write_text("electron.exe", encoding="utf-8")
        _patch_dirs(monkeypatch, tmp_path, tmp_path)
        with caplog.at_level("WARNING"):
            assert electron_launcher.resolve_electron_launch() is None
        assert "pnpm rebuild electron" in caplog.text

    def test_tudo_presente_resolve_executavel_e_main_js(
        self, tmp_path: Path, monkeypatch
    ):
        electron_dir = tmp_path / "electron"
        electron_dir.mkdir()
        (electron_dir / "dist").mkdir()
        main_js = electron_dir / "dist" / "main.js"
        main_js.write_text("// main", encoding="utf-8")
        node_electron_dist = tmp_path / "node_modules" / "electron" / "dist"
        node_electron_dist.mkdir(parents=True)
        (node_electron_dist.parent / "path.txt").write_text(
            "electron.exe", encoding="utf-8"
        )
        exe = node_electron_dist / "electron.exe"
        exe.write_bytes(b"fake binary")
        _patch_dirs(monkeypatch, tmp_path, electron_dir)

        result = electron_launcher.resolve_electron_launch()

        assert result == (str(exe), [str(main_js)])


@pytest.mark.skipif(
    not electron_launcher.resolve_electron_launch(),
    reason=_ELECTRON_DEV_UNAVAILABLE_REASON,
)
def test_resolve_no_repo_real_aponta_para_arquivos_que_existem():
    result = electron_launcher.resolve_electron_launch()
    assert result is not None
    exe, args = result
    assert Path(exe).is_file()
    assert len(args) == 1
    assert Path(args[0]).is_file()


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Servidor HTTP mínimo simulando o /health do backend real — usado só
    pra confirmar que o Electron spawnado de verdade bate nele."""

    hit_event: threading.Event

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            self.hit_event.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # silencia o log padrão do BaseHTTPRequestHandler


@pytest.mark.electron_dev
@pytest.mark.skipif(
    not electron_launcher.resolve_electron_launch(),
    reason=_ELECTRON_DEV_UNAVAILABLE_REASON,
)
def test_electron_dev_spawnado_em_modo_attached_conecta_ao_backend_real():
    """Spawna o Electron dev build de verdade (binário real, não mockado)
    em modo "attached" (VECTORA_EXTERNAL_BACKEND=1) contra um servidor
    HTTP real respondendo /health, e confirma que o processo Electron
    de fato bate no health check — prova que resolveExternalBackendConnection
    + waitForBackendReady (electron/src/backend-lifecycle.ts) funcionam de
    ponta a ponta contra um binário real."""
    launch = electron_launcher.resolve_electron_launch()
    assert launch is not None
    exe, args = launch

    hit_event = threading.Event()
    handler_cls = type(
        "_TestHealthHandler", (_HealthHandler,), {"hit_event": hit_event}
    )

    with tempfile.TemporaryDirectory(prefix="vectora-electron-test-") as user_data:
        # Fora do Windows o Electron fala com o backend pelo socket Unix
        # `<VECTORA_HOME>/vectora.sock` (e ignora a porta); no Windows, por TCP.
        env = {**os.environ, "VECTORA_EXTERNAL_BACKEND": "1", "VECTORA_HOME": user_data}
        if sys.platform == "win32":
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            env["VECTORA_PORT"] = str(port)
            server: socketserver.BaseServer = http.server.HTTPServer(
                ("127.0.0.1", port), handler_cls
            )
        else:
            server = socketserver.UnixStreamServer(
                str(Path(user_data) / "vectora.sock"), handler_cls
            )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # userData próprio: sem isto o `requestSingleInstanceLock()` de main.ts
        # perde pro Vectora instalado se ele estiver aberto na máquina, e a
        # instância do teste sai com código 0 sem nunca chegar ao health check —
        # falha que parece regressão de código mas é só contaminação de ambiente.
        proc = subprocess.Popen(  # noqa: S603  # nosec B603
            [exe, *args, f"--user-data-dir={user_data}"], env=env
        )
        try:
            connected = hit_event.wait(timeout=30)
            assert connected, (
                f"Electron não bateu em /health em 30s "
                f"(exit code do processo: {proc.poll()})"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            server.shutdown()
            server_thread.join(timeout=5)
