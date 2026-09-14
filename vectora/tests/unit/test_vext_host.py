import json
from pathlib import Path

import pytest

from backend.services.vext import VextManifest
from backend.services.vext_artifact import build_vext
from backend.services.vext_host import VextHost


def _source(root: Path, permissions: list[str]) -> None:
    (root / "main.py").write_text(
        "def handle(method, params):\n"
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
