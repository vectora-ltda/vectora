"""Contratos determinísticos do Docker Model Runner.

As chamadas HTTP usam ``httpx.MockTransport`` apenas para testar o parser do
contrato. O teste live opcional fica separado e só roda quando explicitamente
habilitado pelo ambiente.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock

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
async def test_docker_host_info_reports_runtime_and_gpu_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (
                (
                    b'{"OSType":"linux","Architecture":"aarch64",'
                    b'"ServerVersion":"29.0","NCPU":8,"MemTotal":1234,'
                    b'"Runtimes":{"io.containerd.runc.v2":{},"nvidia":{}},'
                    b'"Warnings":["example"]}'
                ),
                b"",
            )

        async def wait(self) -> None:
            return None

    async def create(*args: str, **kwargs: object) -> Process:
        assert args == ("docker", "info", "--format", "{{json .}}")
        assert kwargs.get("shell") is None
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    info = await dmr.docker_host_info()

    assert info["architecture"] == "aarch64"
    assert info["cpus"] == 8
    assert info["gpu_backends"] == ["nvidia"]
    assert info["warnings"] == ["example"]


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
async def test_probe_dmr_inference_supports_openai_and_ollama_contracts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {}}]})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "pong"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    assert await dmr.probe_dmr_inference(
        None, "openai", "hf.co/Qwen/Qwen3-0.6B", transport=transport
    )
    assert await dmr.probe_dmr_inference(
        None, "ollama", "hf.co/Qwen/Qwen3-0.6B", transport=transport
    )


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

    async def fake_host_info() -> dict[str, object]:
        return {
            "os": "linux",
            "architecture": "aarch64",
            "cpus": 8,
            "memory_bytes": 1234,
            "runtimes": ["nvidia"],
            "gpu_backends": ["nvidia"],
            "warnings": [],
        }

    monkeypatch.setattr(settings, "dmr_base_url", None)
    monkeypatch.setattr(
        "backend.services.docker_model_runner.docker_model_available",
        fake_available,
    )
    monkeypatch.setattr(
        "backend.services.docker_model_runner.probe_dmr",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.services.docker_model_runner.docker_host_info",
        fake_host_info,
    )

    status = await provider_routing.get_dmr_status()

    assert status["configured"] is False
    assert status["base_url"] == dmr.DEFAULT_DMR_BASE_URL
    assert status["reachable"] is True
    assert status["models"] == ["hf.co/Qwen/Qwen3-0.6B"]
    assert status["state"] == "ready"
    assert status["architecture"]
    assert status["backend"] == "nvidia"
    capabilities = status["capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["cpus"] == 8


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
async def test_run_model_configures_context_before_detached_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, command_timeout: float = 120.0) -> str:
        calls.append(args)
        return "ok"

    monkeypatch.setattr(dmr, "run_docker_model", fake_run)

    assert await dmr.run_model("hf.co/Qwen/Qwen3-0.6B", 8192) == "ok"
    assert calls == [
        ("configure", "--context-size", "8192", "hf.co/Qwen/Qwen3-0.6B"),
        ("run", "--detach", "hf.co/Qwen/Qwen3-0.6B"),
    ]


@pytest.mark.asyncio
async def test_model_job_reports_completion_and_keeps_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, command_timeout: float = 120.0) -> str:
        calls.append(args)
        return "ok"

    monkeypatch.setattr(dmr, "run_docker_model", fake_run)
    monkeypatch.setattr(
        dmr,
        "probe_dmr",
        AsyncMock(
            return_value=dmr.DmrProbe(True, "openai", ("hf.co/Qwen/Qwen3-0.6B",))
        ),
    )
    monkeypatch.setattr(dmr, "probe_dmr_inference", AsyncMock(return_value=True))
    job = await dmr.create_model_job("hf.co/Qwen/Qwen3-0.6B", "start")
    for _ in range(100):
        if job.status not in {"queued", "running"}:
            break
        await asyncio.sleep(0.01)
    assert job.status == "completed"
    assert job.output == "ok\nok"
    assert calls == [
        ("pull", "hf.co/Qwen/Qwen3-0.6B"),
        ("run", "--detach", "hf.co/Qwen/Qwen3-0.6B"),
        ("inspect", "hf.co/Qwen/Qwen3-0.6B"),
    ]


@pytest.mark.asyncio
async def test_model_job_fails_when_readiness_inference_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*args: str, command_timeout: float = 120.0) -> str:
        return "ok"

    monkeypatch.setattr(dmr, "run_docker_model", fake_run)
    monkeypatch.setattr(
        dmr,
        "probe_dmr",
        AsyncMock(return_value=dmr.DmrProbe(True, "openai", ("hf.co/model",))),
    )
    monkeypatch.setattr(dmr, "probe_dmr_inference", AsyncMock(return_value=False))
    stop = AsyncMock(return_value="stopped")
    monkeypatch.setattr(dmr, "stop_model", stop)
    job = await dmr.create_model_job("hf.co/model", "start")
    for _ in range(100):
        if job.status not in {"queued", "running"}:
            break
        await asyncio.sleep(0.01)
    assert job.status == "failed"
    assert "readiness" in (job.error or "")
    stop.assert_awaited_once_with("hf.co/model")


@pytest.mark.asyncio
async def test_model_jobs_are_recovered_as_interrupted_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    dmr._jobs.clear()
    dmr._job_tasks.clear()
    dmr._jobs_loaded = False
    job_id = "a" * 32
    (tmp_path / "docker-model-runner-jobs.json").write_text(
        '{"jobs":[{"id":"'
        + job_id
        + '","operation":"start","reference":"hf.co/Qwen/Qwen3-0.6B",'
        '"status":"running","output":"pulling","metadata":{}}]}',
        encoding="utf-8",
    )

    await dmr.restore_model_jobs()

    job = dmr.get_model_job(job_id)
    assert job is not None
    assert job.status == "interrupted"
    assert "reinício" in (job.error or "")

    persisted = (tmp_path / "docker-model-runner-jobs.json").read_text(encoding="utf-8")
    assert '"status":"interrupted"' in persisted


@pytest.mark.asyncio
async def test_persist_jobs_keeps_active_and_only_recent_terminal_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    dmr._jobs.clear()
    dmr._job_tasks.clear()
    dmr._jobs_loaded = False
    for index in range(dmr.MAX_PERSISTED_JOBS + 5):
        job = dmr.DmrJob(
            id=f"{index:032x}",
            operation="prepare",
            reference=f"hf.co/model-{index}",
            context_size=None,
            progress=100,
            phase="completed",
            status="completed",
        )
        dmr._jobs[job.id] = job
    active = dmr.DmrJob(
        id="f" * 32,
        operation="start",
        reference="hf.co/active",
        context_size=None,
        progress=50,
        phase="starting",
        status="running",
    )
    dmr._jobs[active.id] = active

    await dmr._persist_jobs()

    assert active.id in dmr._jobs
    terminal = [job for job in dmr._jobs.values() if job.status == "completed"]
    assert len(terminal) == dmr.MAX_PERSISTED_JOBS


@pytest.mark.asyncio
async def test_dmr_live_pull_run_and_infer_when_explicitly_enabled() -> None:
    """Exercita pull, preload e inferência contra um DMR real.

    O teste permanece opt-in porque requer Docker Model Runner local e baixa um
    modelo. Quando habilitado, nenhum contrato HTTP é simulado.
    """
    if os.getenv("VECTORA_TEST_DMR_LIVE") != "1":
        pytest.skip(
            "ative VECTORA_TEST_DMR_LIVE=1 para testar Docker Model Runner real"
        )
    reference = os.getenv("VECTORA_TEST_DMR_MODEL", "ai/smollm2")
    base_url = os.getenv("DMR_BASE_URL")
    available, detail = await dmr.docker_model_available()
    assert available, detail
    await dmr.prepare_model(reference)
    try:
        await dmr.run_model(reference)
        result = await dmr.probe_dmr(base_url)
        assert result.reachable, result.detail
        assert result.contract in {"openai", "ollama"}
        candidates = set(result.models)
        model = (
            reference if reference in candidates else reference.removeprefix("hf.co/")
        )
        assert model in candidates, result.models
        assert await dmr.probe_dmr_inference(base_url, result.contract, model), (
            "DMR não concluiu uma inferência real"
        )
    finally:
        with suppress(OSError, RuntimeError, ValueError):
            await dmr.stop_model(reference)
