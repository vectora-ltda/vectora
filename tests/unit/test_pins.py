"""Pins de sessão — persistência no backend.

Contrato:
1. _set_session_pins() grava a lista em vectora_sessions.extra["pins"],
   mesclando (preserva title/mode/workspace_id já gravados).
2. _get_session_pins() lê a lista de volta; thread inexistente → [].
3. Normalização: dedup, trim, backslash→slash, descarta vazios.
4. Endpoints GetThreadPins/SetThreadPins fazem o round-trip via RPC.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from backend.api.handlers import threads


@pytest.fixture
async def fake_db():
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """
        CREATE TABLE vectora_sessions (
            thread_id     TEXT PRIMARY KEY,
            user_type     TEXT,
            created_at    TEXT,
            last_activity TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            extra         TEXT    NOT NULL DEFAULT '{}',
            mode          TEXT    NOT NULL DEFAULT 'code'
        )
        """
    )
    await db.commit()
    try:
        yield db
    finally:
        await db.close()


async def _extra(db: aiosqlite.Connection, thread_id: str) -> dict:
    async with db.execute(
        "SELECT extra FROM vectora_sessions WHERE thread_id = ?", (thread_id,)
    ) as cur:
        row = await cur.fetchone()
    return json.loads(row[0]) if row else {}


# ---------------------------------------------------------------------------
# Helpers de persistência
# ---------------------------------------------------------------------------


class TestSessionPinsPersistence:
    async def test_set_then_get_roundtrip(self, fake_db):
        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            await threads._set_session_pins("t1", ["src/a.py", "README.md"])
            pins = await threads._get_session_pins("t1")
        assert pins == ["src/a.py", "README.md"]

    async def test_get_missing_thread_is_empty(self, fake_db):
        """Borda: thread sem registro → lista vazia, nunca erro."""
        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            assert await threads._get_session_pins("nao-existe") == []

    async def test_set_preserves_other_extra_keys(self, fake_db):
        """Pins não podem apagar title/mode/workspace_id já gravados."""
        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            await threads._upsert_session(
                "t2", title="Olá", workspace_id="ws1", mode="dev"
            )
            await threads._set_session_pins("t2", ["a.py"])
            extra = await _extra(fake_db, "t2")
        assert extra["title"] == "Olá"
        assert extra["workspace_id"] == "ws1"
        assert extra["mode"] == "dev"
        assert extra["pins"] == ["a.py"]

    async def test_normalizes_dedup_trim_slash_and_drops_empty(self, fake_db):
        """Erro/borda: entrada suja (duplicados, espaços, backslash, vazios)."""
        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            cleaned = await threads._set_session_pins(
                "t3",
                ["src\\a.py", "  src/a.py  ", "", "   ", "b.py", "b.py"],
            )
        assert cleaned == ["src/a.py", "b.py"]

    async def test_set_empty_clears(self, fake_db):
        """Borda: setar lista vazia zera os pins."""
        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            await threads._set_session_pins("t4", ["a.py"])
            await threads._set_session_pins("t4", [])
            assert await threads._get_session_pins("t4") == []


# ---------------------------------------------------------------------------
# Endpoints RPC
# ---------------------------------------------------------------------------


class TestThreadPinsEndpoints:
    async def test_set_and_get_via_rpc(self, fake_db):
        from backend.api.schemas import GetThreadPinsRequest, SetThreadPinsRequest

        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            set_resp = await threads.set_thread_pins(
                SetThreadPinsRequest(thread_id="t5", pins=["x.py", "x.py"])
            )
            get_resp = await threads.get_thread_pins(
                GetThreadPinsRequest(thread_id="t5")
            )
        assert set_resp.pins == ["x.py"]
        assert get_resp.thread_id == "t5"
        assert get_resp.pins == ["x.py"]

    async def test_get_via_rpc_missing_thread(self, fake_db):
        from backend.api.schemas import GetThreadPinsRequest

        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            resp = await threads.get_thread_pins(GetThreadPinsRequest(thread_id="zzz"))
        assert resp.pins == []
        assert resp.thread_id == "zzz"

    async def test_set_via_rpc_normalizes_and_dedups(self, fake_db):
        from backend.api.schemas import SetThreadPinsRequest

        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            resp = await threads.set_thread_pins(
                SetThreadPinsRequest(
                    thread_id="t6", pins=["a\\b.py", " a/b.py ", "a/b.py", ""]
                )
            )
        assert resp.pins == ["a/b.py"]

    async def test_set_via_rpc_default_empty_pins(self, fake_db):
        from backend.api.schemas import SetThreadPinsRequest

        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            resp = await threads.set_thread_pins(SetThreadPinsRequest(thread_id="t7"))
        assert resp.pins == []

    async def test_set_overwrites_previous(self, fake_db):
        from backend.api.schemas import SetThreadPinsRequest

        with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
            await threads.set_thread_pins(
                SetThreadPinsRequest(thread_id="t8", pins=["a.py", "b.py"])
            )
            resp = await threads.set_thread_pins(
                SetThreadPinsRequest(thread_id="t8", pins=["c.py"])
            )
        assert resp.pins == ["c.py"]


# ---------------------------------------------------------------------------
# _normalize_pins — função pura
# ---------------------------------------------------------------------------


class TestNormalizePins:
    def test_empty_list(self):
        assert threads._normalize_pins([]) == []

    def test_backslash_to_posix(self):
        assert threads._normalize_pins(["src\\a\\b.py"]) == ["src/a/b.py"]

    def test_trims_whitespace(self):
        assert threads._normalize_pins(["  a.py  ", "\tb.py\n"]) == ["a.py", "b.py"]

    def test_drops_empty_and_blank(self):
        assert threads._normalize_pins(["", "   ", "\t", "a.py"]) == ["a.py"]

    def test_dedup_preserves_first_order(self):
        assert threads._normalize_pins(["b.py", "a.py", "b.py", "a.py"]) == [
            "b.py",
            "a.py",
        ]

    def test_dedup_after_normalization(self):
        # "a\\b.py" e "a/b.py" são o mesmo após normalização
        assert threads._normalize_pins(["a\\b.py", "a/b.py"]) == ["a/b.py"]

    def test_keeps_distinct(self):
        assert threads._normalize_pins(["a.py", "b.py", "c.py"]) == [
            "a.py",
            "b.py",
            "c.py",
        ]

    def test_many_with_duplicates(self):
        raw = [f"f{i % 10}.py" for i in range(100)]
        assert threads._normalize_pins(raw) == [f"f{i}.py" for i in range(10)]


# ---------------------------------------------------------------------------
# build_pinned_context — injeção do conteúdo no contexto
# ---------------------------------------------------------------------------


async def _set_pins(fake_db, thread_id: str, pins: list[str]) -> None:
    with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
        await threads._set_session_pins(thread_id, pins)


async def _build(fake_db, thread_id: str, cwd, **kw) -> str:
    with patch.object(threads, "_get_db", AsyncMock(return_value=fake_db)):
        return await threads.build_pinned_context(thread_id, cwd, **kw)


class TestBuildPinnedContext:
    async def test_single_pin_includes_content(self, fake_db, tmp_path):
        (tmp_path / "a.py").write_text("print('oi')", encoding="utf-8")
        await _set_pins(fake_db, "t", ["a.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "<pinned_files>" in block
        assert "</pinned_files>" in block
        assert '<file path="a.py">' in block
        assert "print('oi')" in block

    async def test_multiple_pins_in_order(self, fake_db, tmp_path):
        (tmp_path / "a.py").write_text("AAA", encoding="utf-8")
        (tmp_path / "b.py").write_text("BBB", encoding="utf-8")
        await _set_pins(fake_db, "t", ["a.py", "b.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert block.index('path="a.py"') < block.index('path="b.py"')
        assert "AAA" in block and "BBB" in block

    async def test_no_pins_returns_empty(self, fake_db, tmp_path):
        block = await _build(fake_db, "sem-pins", tmp_path)
        assert block == ""

    async def test_no_workspace_returns_empty(self, fake_db, tmp_path):
        await _set_pins(fake_db, "t", ["a.py"])
        assert await _build(fake_db, "t", None) == ""
        assert await _build(fake_db, "t", "") == ""

    async def test_missing_file_skipped(self, fake_db, tmp_path):
        (tmp_path / "exists.py").write_text("HERE", encoding="utf-8")
        await _set_pins(fake_db, "t", ["exists.py", "ghost.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "HERE" in block
        assert "ghost.py" not in block

    async def test_binary_file_skipped(self, fake_db, tmp_path):
        (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02BINARY")
        (tmp_path / "ok.py").write_text("OK", encoding="utf-8")
        await _set_pins(fake_db, "t", ["bin.dat", "ok.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "OK" in block
        assert "bin.dat" not in block

    async def test_large_file_truncated(self, fake_db, tmp_path):
        (tmp_path / "big.txt").write_text("x" * 5000, encoding="utf-8")
        await _set_pins(fake_db, "t", ["big.txt"])
        block = await _build(fake_db, "t", tmp_path, cap=100)
        assert "… (truncado)" in block
        assert block.count("x") <= 200

    async def test_directory_pin_skipped(self, fake_db, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "f.py").write_text("F", encoding="utf-8")
        await _set_pins(fake_db, "t", ["sub", "f.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "F" in block
        assert 'path="sub"' not in block

    async def test_nested_file_read(self, fake_db, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "deep.py").write_text("DEEP", encoding="utf-8")
        await _set_pins(fake_db, "t", ["src/deep.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "DEEP" in block

    async def test_path_traversal_skipped(self, fake_db, tmp_path):
        outside = tmp_path.parent / "secret_pin_test.txt"
        outside.write_text("SECRET", encoding="utf-8")
        try:
            await _set_pins(fake_db, "t", ["../secret_pin_test.txt"])
            block = await _build(fake_db, "t", tmp_path)
            assert "SECRET" not in block
            assert block == ""
        finally:
            outside.unlink(missing_ok=True)

    async def test_unicode_preserved(self, fake_db, tmp_path):
        (tmp_path / "u.py").write_text("café ☕ 日本語", encoding="utf-8")
        await _set_pins(fake_db, "t", ["u.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert "café ☕ 日本語" in block

    async def test_empty_file_still_block(self, fake_db, tmp_path):
        (tmp_path / "empty.py").write_text("", encoding="utf-8")
        await _set_pins(fake_db, "t", ["empty.py"])
        block = await _build(fake_db, "t", tmp_path)
        assert '<file path="empty.py">' in block

    async def test_all_invalid_returns_empty(self, fake_db, tmp_path):
        await _set_pins(fake_db, "t", ["ghost1.py", "ghost2.py"])
        assert await _build(fake_db, "t", tmp_path) == ""

    async def test_get_pins_raises_returns_empty(self, tmp_path):
        with patch.object(
            threads, "_get_session_pins", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            block = await threads.build_pinned_context("t", tmp_path)
        assert block == ""

    async def test_cap_default_applies(self, fake_db, tmp_path):
        (tmp_path / "big.txt").write_text("y" * 9000, encoding="utf-8")
        await _set_pins(fake_db, "t", ["big.txt"])
        block = await _build(fake_db, "t", tmp_path)
        assert "… (truncado)" in block


# ---------------------------------------------------------------------------
# _prepend_text_context (chat.py) — injeção no VMessage
# ---------------------------------------------------------------------------


class TestPrependTextContext:
    def test_prepends_text_block(self):
        from backend.api.handlers.chat import _prepend_text_context
        from backend.vtypes.message import ContentBlock, MessageRole, VMessage

        msg = VMessage(
            role=MessageRole.USER,
            content=[ContentBlock(kind="text", text="pergunta")],
        )
        out = _prepend_text_context(msg, "CTX")
        assert len(out.content) == 2
        assert out.content[0].kind == "text"
        assert out.content[0].text == "CTX"
        assert out.content[1].text == "pergunta"

    def test_text_part_first_preserves_multimodal(self):
        from backend.api.handlers.chat import _prepend_text_context
        from backend.vtypes.message import ContentBlock, MessageRole, VMessage

        msg = VMessage(
            role=MessageRole.USER,
            content=[
                ContentBlock(kind="text", text="oi"),
                ContentBlock(kind="image_url", image_url="data:image/png;base64,x"),
            ],
        )
        out = _prepend_text_context(msg, "CTX")
        assert out.content[0].kind == "text"
        assert out.content[0].text == "CTX"
        assert out.content[1].kind == "text"
        assert out.content[1].text == "oi"
        assert out.content[2].kind == "image_url"

    def test_preserves_all_parts(self):
        from backend.api.handlers.chat import _prepend_text_context
        from backend.vtypes.message import ContentBlock, MessageRole, VMessage

        msg = VMessage(
            role=MessageRole.USER,
            content=[ContentBlock(kind="text", text=str(i)) for i in range(5)],
        )
        out = _prepend_text_context(msg, "C")
        assert len(out.content) == 6
        assert out.content[0].text == "C"
        assert [b.text for b in out.content[1:]] == [str(i) for i in range(5)]
