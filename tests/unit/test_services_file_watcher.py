"""File Watcher.

Um watcher por workspace: detecta mudancas em disco com debounce 300ms
e publica via KV pub/sub para que o SSE invalide os tabs do frontend.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import backend.scheduling.file_watcher as fw_module
from backend.scheduling.file_watcher import FileWatcher, debounce_collect

# ---------------------------------------------------------------------------
# debounce_collect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_collect_batches_rapid_events():
    """Eventos em <300ms devem ser agrupados em um único callback."""
    received: list[set] = []

    async def on_change(paths: set) -> None:
        received.append(paths)

    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(debounce_collect(q, on_change, debounce_ms=50))

    await q.put("file_a.py")
    await q.put("file_b.py")
    await asyncio.sleep(0.1)  # aguarda debounce

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(received) == 1
    assert "file_a.py" in received[0]
    assert "file_b.py" in received[0]


@pytest.mark.asyncio
async def test_debounce_collect_emits_separate_batches_for_spaced_events():
    """Eventos espaçados >debounce_ms devem gerar callbacks separados."""
    received: list[set] = []

    async def on_change(paths: set) -> None:
        received.append(paths)

    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(debounce_collect(q, on_change, debounce_ms=50))

    await q.put("file_a.py")
    await asyncio.sleep(0.1)
    await q.put("file_b.py")
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(received) == 2


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_watcher_start_stop(tmp_path: Path):
    """FileWatcher deve iniciar e parar sem erros."""
    watcher = FileWatcher(str(tmp_path), workspace_id="ws-1")
    await watcher.start()
    assert watcher.running
    await watcher.stop()
    assert not watcher.running


@pytest.mark.asyncio
async def test_file_watcher_publishes_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ao detectar mudanca, deve publicar via KV."""
    published: list[dict] = []

    async def fake_kv_publish(workspace_id: str, channel: str, payload: dict) -> None:
        published.append({"workspace_id": workspace_id, "channel": channel, **payload})

    monkeypatch.setattr(fw_module, "_kv_publish", fake_kv_publish)

    watcher = FileWatcher(str(tmp_path), workspace_id="ws-42")
    await watcher._on_changes({"file.py"})

    assert len(published) == 1
    assert published[0]["workspace_id"] == "ws-42"
    assert "files" in published[0].get("tabs", [])


@pytest.mark.asyncio
async def test_file_watcher_singleton_per_workspace(tmp_path: Path):
    """get_watcher retorna a mesma instancia para o mesmo workspace_id."""
    from backend.scheduling.file_watcher import get_watcher

    w1 = get_watcher(str(tmp_path), workspace_id="ws-singleton")
    w2 = get_watcher(str(tmp_path), workspace_id="ws-singleton")
    assert w1 is w2
