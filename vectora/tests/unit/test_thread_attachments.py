"""Anexos de imagem no histórico de thread via REST.

Cobre dois pontos: `GET /threads/{id}/history` devolve os `attachments` de
cada mensagem (antes descartados na conversão pra `HistoryMessage`), e
`GET /threads/{id}/attachments/{filename}` serve o arquivo persistido por
`_persist_image_attachment` (`chat.py`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.schemas import Thread
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


@pytest.mark.asyncio
async def test_history_paginated_propaga_attachments(monkeypatch):
    """Regressão: `_att` era descartado na list comprehension — attachments
    do histórico nunca chegavam no frontend, mesmo já persistidos."""
    from backend.api.handlers import threads as threads_mod

    async def _fake_get_thread(request):
        return Thread(id=request.thread_id, created_at="", updated_at="")

    monkeypatch.setattr(threads_mod, "get_thread", _fake_get_thread)

    attachments_meta = [
        {
            "name": "screenshot.png",
            "mimeType": "image/png",
            "kind": "image",
            "size": 1024,
            "url": "/threads/t1/attachments/abc123.png",
        }
    ]
    fake_pairs = [("human", "veja isso", "cp1", attachments_meta)]

    monkeypatch.setattr(
        "backend.services.agent_factory.aget_thread_messages",
        AsyncMock(return_value=fake_pairs),
    )

    resp = await threads_mod.get_thread_history_paginated("t1")

    assert len(resp.messages) == 1
    assert resp.messages[0].attachments == attachments_meta


@pytest.mark.asyncio
async def test_history_paginated_mensagem_sem_attachments_fica_vazia(monkeypatch):
    from backend.api.handlers import threads as threads_mod

    async def _fake_get_thread(request):
        return Thread(id=request.thread_id, created_at="", updated_at="")

    monkeypatch.setattr(threads_mod, "get_thread", _fake_get_thread)
    monkeypatch.setattr(
        "backend.services.agent_factory.aget_thread_messages",
        AsyncMock(return_value=[("human", "oi", "cp1", [])]),
    )

    resp = await threads_mod.get_thread_history_paginated("t1")

    assert resp.messages[0].attachments == []


@pytest.mark.asyncio
async def test_reidrata_metadados_completos_do_anexo(monkeypatch):
    from backend.services import agent_factory

    class Store:
        async def get_history_with_ids(self, _thread_id):
            return [
                (
                    "msg-1",
                    VMessage(
                        role=MessageRole.USER,
                        content=[
                            ContentBlock(
                                kind="image_url",
                                asset_id="asset-1",
                                attachment_name="foto.png",
                                attachment_mime_type="image/png",
                                attachment_size_bytes=321,
                            )
                        ],
                    ),
                )
            ]

    monkeypatch.setattr(agent_factory, "get_session_store", lambda: _store(Store()))
    result = await agent_factory.aget_thread_messages("thread-1")
    assert result[0][3][0] == {
        "kind": "image",
        "name": "foto.png",
        "mimeType": "image/png",
        "size": 321,
        "url": "/threads/thread-1/assets/asset-1",
        "asset_id": "asset-1",
        "attachment_name": "foto.png",
    }


async def _store(value):
    return value


class TestGetThreadAttachment:
    @staticmethod
    def _request() -> Request:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/threads/t1/attachments/abc123.png",
                "headers": [],
                "query_string": b"",
            }
        )
        request.state.user = type("User", (), {"id": "owner", "role": "member"})()
        return request

    @pytest.mark.asyncio
    async def test_serve_arquivo_existente(self, tmp_path, monkeypatch):
        from backend.api.handlers import threads as threads_mod
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        target_dir = tmp_path / "chat-attachments" / "t1"
        target_dir.mkdir(parents=True)
        (target_dir / "abc123.png").write_bytes(b"\x89PNG-fake-bytes")

        monkeypatch.setattr(threads_mod, "_assert_owns_thread", AsyncMock())
        response = await threads_mod.get_thread_attachment(
            "t1", "abc123.png", self._request()
        )

        assert str(response.path) == str(target_dir / "abc123.png")

    @pytest.mark.asyncio
    async def test_arquivo_inexistente_retorna_404(self, tmp_path, monkeypatch):
        from backend.api.handlers import threads as threads_mod
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)

        with pytest.raises(HTTPException) as exc_info:
            await threads_mod.get_thread_attachment(
                "t1", "nao-existe.png", self._request()
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_no_thread_id_e_sanitizado(
        self, tmp_path, monkeypatch
    ):
        """Erro/borda de segurança: `thread_id`/`filename` vêm direto da URL —
        sem sanitização, `../../` escaparia de `chat-attachments/`."""
        from backend.api.handlers import threads as threads_mod
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        # Arquivo sensível fora de chat-attachments/ — não pode ser servido.
        secret = tmp_path / "secret.txt"
        secret.write_text("segredo")

        with pytest.raises(HTTPException) as exc_info:
            await threads_mod.get_thread_attachment(
                "../..", "secret.txt", self._request()
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_no_filename_e_sanitizado(self, tmp_path, monkeypatch):
        from backend.api.handlers import threads as threads_mod
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("segredo")

        with pytest.raises(HTTPException) as exc_info:
            await threads_mod.get_thread_attachment(
                "t1", "../../secret.txt", self._request()
            )

    @pytest.mark.asyncio
    async def test_rejeita_usuario_sem_posse(self, monkeypatch):
        from backend.api.handlers import threads as threads_mod

        async def reject(_thread_id, _request):
            raise HTTPException(status_code=404, detail="Thread não encontrada")

        monkeypatch.setattr(threads_mod, "_assert_owns_thread", reject)
        with pytest.raises(HTTPException) as exc_info:
            await threads_mod.get_thread_attachment("t1", "abc123.png", self._request())
        assert exc_info.value.status_code == 404

        assert exc_info.value.status_code == 404
