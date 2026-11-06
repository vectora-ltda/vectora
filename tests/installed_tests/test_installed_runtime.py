"""Black-box checks executed by ``vectora --tests`` against the installed app."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

# Log de inicialização de cada backend de teste, para os testes anexarem o
# diagnóstico do app instalado quando uma verificação falha.
_STARTUP_LOGS: dict[str, Path] = {}


def _startup_log_tail(base_url: str, max_chars: int = 6000) -> str:
    path = _STARTUP_LOGS.get(base_url)
    if path is None or not path.is_file():
        return "(log de inicialização indisponível)"
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]


def _installed_executable() -> Path:
    value = os.environ.get("VECTORA_INSTALLED_EXECUTABLE")
    if not value:
        pytest.fail("VECTORA_INSTALLED_EXECUTABLE não foi definido pelo runner")
    executable = Path(value)
    if not executable.is_file():
        pytest.fail(f"Executável instalado não existe: {executable}")
    return executable


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(url: str, timeout: float = 3.0) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Accept": "application/json"}
    )
    try:
        # URL sempre aponta para loopback e porta efêmera criada pelo teste.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8") or "{}")
        return exc.code, payload


@pytest.fixture(scope="module")
def installed_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    executable = _installed_executable()
    runtime_home = tmp_path_factory.mktemp("vectora-installed-home")
    port = _free_loopback_port()
    env = {
        **os.environ,
        "VECTORA_HOME": str(runtime_home),
        "VECTORA_RUNTIME_PROFILE": "installed-test",
        "VECTORA_HEADLESS": "1",
        "VECTORA_LICENSE_BYPASS": "1",
        "VECTORA_PORT": str(port),
    }
    startup_log_path = runtime_home / "startup.log"
    with startup_log_path.open("w+b") as startup_log_file:
        process = subprocess.Popen(  # noqa: S603
            [
                str(executable),
                "start",
                "--headless",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=startup_log_file,
            stderr=subprocess.STDOUT,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
        )
    base_url = f"http://127.0.0.1:{port}"
    _STARTUP_LOGS[base_url] = startup_log_path
    deadline = time.monotonic() + 75
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                startup_log = startup_log_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                pytest.fail(
                    "O backend instalado encerrou antes de ficar pronto "
                    f"(exit {process.returncode}):\n{startup_log}"
                )
            try:
                status, payload = _request_json(f"{base_url}/health/live")
                if status == 200 and payload.get("status") == "live":
                    break
            except (OSError, TimeoutError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        else:
            pytest.fail(
                "O backend instalado não respondeu a /health/live em 75 s:\n"
                + startup_log_path.read_text(encoding="utf-8", errors="replace")
            )
        yield base_url
    finally:
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGINT)
            except (OSError, ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_installed_cli_reports_version() -> None:
    executable = _installed_executable()
    result = subprocess.run(  # noqa: S603
        [str(executable), "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "vectora " in result.stdout.lower()


def test_installed_backend_becomes_live(installed_server: str) -> None:
    status, payload = _request_json(f"{installed_server}/health/live")
    assert status == 200
    assert payload.get("status") == "live"
    assert isinstance(payload.get("version"), str)


def test_installed_storage_is_ready(installed_server: str) -> None:
    # A primeira conexão ao LanceDB pode fazer cold-start no host do usuário:
    # a sonda responde 503 enquanto ela termina, então o teste a consulta de
    # novo até ficar pronta ou estourar o prazo.
    deadline = time.monotonic() + 60
    while True:
        status, payload = _request_json(
            f"{installed_server}/health/ready", timeout=20.0
        )
        if status == 200 or time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    assert status == 200, (
        f"/health/ready respondeu {status}: {payload}\n"
        f"Log do backend instalado:\n{_startup_log_tail(installed_server)}"
    )
    assert payload.get("status") == "ready"


def test_installed_openapi_contains_runtime_routes(installed_server: str) -> None:
    status, payload = _request_json(f"{installed_server}/openapi.json")
    assert status == 200
    routes = payload.get("paths")
    assert isinstance(routes, dict)
    assert "/health/live" in routes
    assert "/health/ready" in routes
    assert "/api/tools/schema" in routes


def test_installed_app_rejects_unknown_command() -> None:
    result = subprocess.run(  # noqa: S603
        [str(_installed_executable()), "__invalid_vectora_test_command__"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr.lower()
