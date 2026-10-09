from pathlib import Path

import pytest

from backend.services.llamacpp_sidecar import start_llamacpp


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
