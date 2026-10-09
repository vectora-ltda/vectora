import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from backend.services.llamacpp_sidecar import (
    _readiness_url,
    llamacpp_status,
    start_llamacpp,
)


def test_llamacpp_status_is_stopped_by_default() -> None:
    status = llamacpp_status()
    assert status["running"] is False
    assert status["pid"] is None


def test_readiness_url_brackets_ipv6_hosts() -> None:
    assert _readiness_url("::1", 8080) == "http://[::1]:8080/v1/models"
    assert _readiness_url("127.0.0.1", 8080) == "http://127.0.0.1:8080/v1/models"


@pytest.mark.asyncio
async def test_start_llamacpp_rejects_missing_runtime(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await start_llamacpp(tmp_path / "llama-server", tmp_path / "model.gguf")


@pytest.mark.asyncio
async def test_start_llamacpp_rejects_non_loopback(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server"
    model = tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    with pytest.raises(ValueError):
        await start_llamacpp(executable, model, host="0.0.0.0")  # noqa: S104


@pytest.mark.asyncio
async def test_start_llamacpp_rejects_different_configuration_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "llama-server"
    model = tmp_path / "model.gguf"
    executable.write_bytes(b"")
    model.write_bytes(b"")

    class RunningProcess:
        returncode = None
        pid = 123

    import backend.services.llamacpp_sidecar as sidecar

    monkeypatch.setattr(sidecar, "_process", RunningProcess())
    monkeypatch.setattr(
        sidecar,
        "_process_spec",
        (str(executable.resolve()), str(model.resolve()), "127.0.0.1", 8080),
    )
    with pytest.raises(RuntimeError, match="outra configuração"):
        await start_llamacpp(executable, model, port=8081)
    monkeypatch.setattr(sidecar, "_process", None)
    monkeypatch.setattr(sidecar, "_process_spec", None)


@pytest.mark.asyncio
async def test_sidecar_persists_and_clears_identity(
    tmp_path: Path, monkeypatch
) -> None:
    import backend.services.llamacpp_sidecar as sidecar

    class RunningProcess:
        pid = 456

    monkeypatch.setenv("VECTORA_HOME", str(tmp_path))
    monkeypatch.setattr(
        sidecar,
        "_process_spec",
        ("/runtime/llama-server", "/models/model.gguf", "127.0.0.1", 8080),
    )
    await sidecar._write_state(cast("asyncio.subprocess.Process", RunningProcess()))
    payload = json.loads((tmp_path / "llamacpp-sidecar.json").read_text())
    assert payload["pid"] == 456
    assert payload["executable"] == "/runtime/llama-server"
    await sidecar._clear_state()
    assert not (tmp_path / "llamacpp-sidecar.json").exists()
    monkeypatch.setattr(sidecar, "_process_spec", None)
