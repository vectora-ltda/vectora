import json
import os
import time
from pathlib import Path

import pytest

from backend.services.vext import VextManifest
from backend.services.vext_artifact import build_vext
from backend.services.vext_host import (
    MAX_QUEUED_RESPONSES,
    OVERSIZED_RESPONSE,
    VextHost,
)


@pytest.fixture(autouse=True)
def _without_system_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os testes do protocolo sem sandbox não dependem de namespaces do host."""
    from backend.services import vext_host

    which = vext_host.shutil_module.which
    monkeypatch.setattr(
        vext_host.shutil_module,
        "which",
        lambda command: None if command == "bwrap" else which(command),
    )


def _source(root: Path, permissions: list[str]) -> None:
    (root / "main.py").write_text(
        "def handle(method, params):\n"
        "    if method == 'empty': return None\n"
        "    return {'method': method, 'params': params}\n",
        encoding="utf-8",
    )
    (root / "vectora-extension.json").write_text(
        json.dumps(
            {
                "id": "host.test",
                "publisher": "test",
                "name": "Host",
                "version": "1.0.0",
                "api_version": 1,
                "protocol_version": 1,
                "runtime": "python",
                "entrypoint": "main.py",
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )


def test_host_runs_python_adapter_out_of_process(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read"])
    artifact = tmp_path / "host.vext"
    build_vext(source, artifact)

    host = VextHost(artifact, allow_unsigned=True, sandbox_required=False)
    try:
        manifest = host.start()
        assert manifest.id == "host.test"
        response = host.request("ping", {"value": 1})
        assert response["result"] == {"method": "ping", "params": {"value": 1}}
        assert host.request("empty")["result"] is None
    finally:
        host.stop()


def test_host_enforces_capabilities_before_rpc(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read"])
    artifact = tmp_path / "host.vext"
    build_vext(source, artifact)

    host = VextHost(artifact, allow_unsigned=True, sandbox_required=False)
    host.start()
    try:
        with pytest.raises(PermissionError, match="network"):
            host.request("fetch", capability="network")
    finally:
        host.stop()


def test_host_does_not_allow_read_capability_to_masquerade_as_write(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read", "workspace.write"])
    artifact = tmp_path / "host.vext"
    build_vext(source, artifact)
    host = VextHost(artifact, allow_unsigned=True, sandbox_required=False)
    host.start()
    try:
        with pytest.raises(PermissionError, match=r"workspace\.write"):
            host.request("write_file", {"path": "x"}, capability="workspace.read")
    finally:
        host.stop()


def test_host_requires_declared_capability_for_privileged_method(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read"])
    artifact = tmp_path / "host.vext"
    build_vext(source, artifact)
    host = VextHost(artifact, allow_unsigned=True, sandbox_required=False)
    host.start()
    try:
        with pytest.raises(PermissionError, match=r"workspace\.write"):
            host.request("write_file", {"path": "x"})
    finally:
        host.stop()


def test_host_requires_os_sandbox_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = VextManifest(
        id="host.test",
        name="Host",
        version="1.0.0",
        api_version=1,
        protocol_version=1,
        entrypoint="main.py",
    )
    monkeypatch.setattr(
        "backend.services.vext_host.shutil_module.which", lambda _: None
    )
    with pytest.raises(RuntimeError, match="sandbox VEXT indisponível"):
        VextHost._sandbox_command(
            ["python", "adapter.py"],
            Path("/tmp/vext"),
            manifest,
            required=True,
        )


def test_host_bounds_noisy_runtime_output() -> None:
    """A noisy child cannot grow the response queue without bound."""
    host = VextHost("unused.vext", sandbox_required=False)
    for index in range(MAX_QUEUED_RESPONSES):
        host._enqueue_response(f"line-{index}")
    host._enqueue_response("overflow")

    assert host._response_queue.qsize() == 1
    assert host._response_queue.get_nowait() == OVERSIZED_RESPONSE


def test_host_rejects_malformed_jsonrpc_response(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read"])
    (source / "main.py").write_text(
        "print('not-json', flush=True)\n"
        "def handle(method, params): return {'ok': True}\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "malformed.vext"
    build_vext(source, artifact)
    host = VextHost(artifact, allow_unsigned=True, sandbox_required=False)
    host.start()
    try:
        with pytest.raises(ValueError, match="resposta JSON-RPC inválida"):
            host.request("ping")
    finally:
        host.stop()


def test_host_timeout_terminates_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read"])
    (source / "main.py").write_text(
        "import time\n"
        "def handle(method, params):\n"
        "    time.sleep(30)\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "timeout.vext"
    build_vext(source, artifact)
    host = VextHost(
        artifact,
        allow_unsigned=True,
        request_timeout=0.1,
        sandbox_required=False,
    )
    host.start()

    with pytest.raises(TimeoutError, match="excedeu o prazo"):
        host.request("slow")

    assert host.process is None
    assert host.root is None


@pytest.mark.skipif(os.name == "nt", reason="verificação de process group POSIX")
def test_host_timeout_kills_descendant_processes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, ["workspace.read", "process.spawn"])
    pid_path = tmp_path / "child.pid"
    (source / "main.py").write_text(
        "import subprocess, sys, time\n"
        "def handle(method, params):\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"    open({str(pid_path)!r}, 'w').write(str(child.pid))\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "descendant.vext"
    build_vext(source, artifact)
    host = VextHost(
        artifact,
        allow_unsigned=True,
        request_timeout=0.2,
        sandbox_required=False,
    )
    host.start()
    with pytest.raises(TimeoutError):
        host.request("spawn")

    child_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("processo descendente continuou ativo após parar o host")
