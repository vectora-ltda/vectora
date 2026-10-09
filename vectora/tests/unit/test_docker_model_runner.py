"""Contratos determinísticos do Docker Model Runner.

As chamadas HTTP usam ``httpx.MockTransport`` apenas para testar o parser do
contrato. O teste live opcional fica separado e só roda quando explicitamente
habilitado pelo ambiente.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from backend.services import docker_model_runner as dmr


def test_validate_model_reference_rejects_command_injection() -> None:
    assert dmr.validate_model_reference("hf.co/Qwen/Qwen3-0.6B") == (
        "hf.co/Qwen/Qwen3-0.6B"
    )
    with pytest.raises(ValueError):
        dmr.validate_model_reference("hf.co/model;rm -rf /")
    with pytest.raises(ValueError):
        dmr.validate_model_reference("--help")


def test_normalize_base_url_rejects_non_http() -> None:
    assert dmr.normalize_base_url("http://127.0.0.1:12434/") == (
        "http://127.0.0.1:12434"
    )
    with pytest.raises(ValueError):
        dmr.normalize_base_url("file:///tmp/docker.sock")


@pytest.mark.asyncio
async def test_probe_prefers_openai_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/engines/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "hf.co/Qwen/Qwen3-0.6B"}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await dmr.probe_dmr(transport=transport)
    assert result.reachable is True
    assert result.contract == "openai"
    assert result.models == ("hf.co/Qwen/Qwen3-0.6B",)


@pytest.mark.asyncio
async def test_provider_status_probes_default_endpoint_without_saved_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import provider_routing
    from backend.settings import settings

    async def fake_available() -> tuple[bool, str]:
        return True, "Docker Model Runner"

    async def fake_probe(*args: object, **kwargs: object) -> dmr.DmrProbe:
        return dmr.DmrProbe(True, "openai", ("hf.co/Qwen/Qwen3-0.6B",))

    monkeypatch.setattr(settings, "dmr_base_url", None)
    monkeypatch.setattr(
        "backend.services.docker_model_runner.docker_model_available",
        fake_available,
    )
    monkeypatch.setattr(
        "backend.services.docker_model_runner.probe_dmr",
        fake_probe,
    )

    status = await provider_routing.get_dmr_status()

    assert status["configured"] is False
    assert status["base_url"] == dmr.DEFAULT_DMR_BASE_URL
    assert status["reachable"] is True
    assert status["models"] == ["hf.co/Qwen/Qwen3-0.6B"]
    assert status["state"] == "ready"
    assert status["architecture"]


@pytest.mark.asyncio
async def test_provider_status_distinguishes_missing_model_runner_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import provider_routing
    from backend.settings import settings

    async def fake_available() -> tuple[bool, str]:
        return False, "docker: unknown command: model"

    async def fake_probe(*args: object, **kwargs: object) -> dmr.DmrProbe:
        return dmr.DmrProbe(False, None, (), "endpoint indisponível")

    monkeypatch.setattr(settings, "dmr_base_url", None)
    monkeypatch.setattr(
        "backend.services.docker_model_runner.docker_model_available",
        fake_available,
    )
    monkeypatch.setattr(
        "backend.services.docker_model_runner.probe_dmr",
        fake_probe,
    )

    status = await provider_routing.get_dmr_status()

    assert status["state"] == "plugin_unavailable"


@pytest.mark.asyncio
async def test_run_docker_model_uses_exec_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], bool]] = []

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"ok", b""

        async def wait(self) -> None:
            return None

    async def create(*args: str, **kwargs: object) -> Process:
        calls.append((args, bool(kwargs.get("shell"))))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    output = await dmr.run_docker_model("pull", "hf.co/Qwen/Qwen3-0.6B")
    assert output == "ok\n"
    assert calls == [(("docker", "model", "pull", "hf.co/Qwen/Qwen3-0.6B"), False)]


@pytest.mark.asyncio
async def test_run_model_preloads_in_detached_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, command_timeout: float = 120.0) -> str:
        calls.append(args)
        return "started"

    monkeypatch.setattr(dmr, "run_docker_model", fake_run)

    assert await dmr.run_model("hf.co/Qwen/Qwen3-0.6B") == "started"
    assert calls == [("run", "--detach", "hf.co/Qwen/Qwen3-0.6B")]


@pytest.mark.asyncio
async def test_model_job_reports_completion_and_keeps_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, command_timeout: float = 120.0) -> str:
        calls.append(args)
        return "ok"

    monkeypatch.setattr(dmr, "run_docker_model", fake_run)
    job = await dmr.create_model_job("hf.co/Qwen/Qwen3-0.6B", "start")
    for _ in range(20):
        if job.status not in {"queued", "running"}:
            break
        await asyncio.sleep(0)
    assert job.status == "completed"
    assert job.output == "ok\nok"
    assert calls == [
        ("pull", "hf.co/Qwen/Qwen3-0.6B"),
        ("run", "--detach", "hf.co/Qwen/Qwen3-0.6B"),
        ("inspect", "hf.co/Qwen/Qwen3-0.6B"),
    ]


@pytest.mark.asyncio
async def test_dmr_live_probe_when_explicitly_enabled() -> None:
    """Smoke test contra Docker real; nunca substitui os contratos determinísticos."""
    if os.getenv("VECTORA_TEST_DMR_LIVE") != "1":
        pytest.skip(
            "ative VECTORA_TEST_DMR_LIVE=1 para testar Docker Model Runner real"
        )
    available, detail = await dmr.docker_model_available()
    assert available, detail
    result = await dmr.probe_dmr(os.getenv("DMR_BASE_URL"))
    assert result.reachable, result.detail
