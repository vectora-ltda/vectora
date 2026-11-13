"""Anexos que não são imagem: classificação, extração e referência."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from backend.api.handlers.chat import _build_user_vmessage
from backend.api.schemas import Attachment, AttachmentKind
from backend.services.assets import AssetStore
from backend.services.media_hydration import hydrate_media_blocks
from backend.services.media_types import classify, effective_media_info
from backend.settings import settings


def _attachment(kind: AttachmentKind, name: str, mime: str, data: bytes) -> Attachment:
    return Attachment(
        kind=kind,
        name=name,
        mime_type=mime,
        base64_data=base64.b64encode(data).decode(),
    )


def _pdf_with_text(text: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _docx_with_text(text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    return tmp_path


class TestClassify:
    def test_assinatura_prevalece_sobre_o_mime_declarado(self) -> None:
        info = classify(b"%PDF-1.7\n...", "nota.txt", "text/plain")
        assert (info.kind, info.mime_type) == ("document", "application/pdf")

    def test_docx_e_identificado_dentro_do_zip(self) -> None:
        info = classify(_docx_with_text("oi"), "a.docx", "")
        assert info.kind == "document"
        assert info.mime_type.endswith("wordprocessingml.document")

    def test_texto_utf8_e_binario_desconhecido(self) -> None:
        assert classify(b"a,b\n1,2\n", "dados.csv", "text/csv").kind == "text"
        assert classify(b"MZ\x90\x00\x00", "app.exe", "").kind == "file"

    def test_gravacao_de_voz_webm_e_audio(self) -> None:
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 16
        assert classify(webm, "voz.webm", "audio/webm").kind == "audio"
        assert classify(webm, "clip.webm", "video/webm").kind == "video"

    def test_tipo_declarado_decide_audio_sem_assinatura(self) -> None:
        info = effective_media_info(b"fake", "memo.mp3", "audio/mpeg", "audio")
        assert info.kind == "audio"


async def test_pdf_real_tem_texto_extraido_e_asset_salvo(home: Path) -> None:
    pdf = _pdf_with_text("Receita trimestral subiu")
    message = await _build_user_vmessage(
        "resuma",
        [_attachment(AttachmentKind.FILE, "relatorio.pdf", "application/pdf", pdf)],
        "thread-pdf",
        user_id="u1",
        workspace_id="w1",
    )
    block = message.content[1]
    assert block.kind == "media"
    assert block.media_kind == "document"
    assert block.attachment_mime_type == "application/pdf"
    assert block.text is not None and "Receita trimestral subiu" in block.text
    assert block.asset_id and block.media_sha256
    assert (home / "artifacts" / "thread-pdf" / "media").is_dir()
    # O texto bruto do PDF não vaza como antes (decodificação UTF-8 cega).
    assert "%PDF" not in block.text


async def test_docx_tem_texto_extraido(home: Path) -> None:
    message = await _build_user_vmessage(
        "leia",
        [
            _attachment(
                AttachmentKind.DOCUMENT,
                "contrato.docx",
                "application/octet-stream",
                _docx_with_text("Cláusula de rescisão"),
            )
        ],
        "thread-docx",
        user_id="u1",
        workspace_id="w1",
    )
    assert "Cláusula de rescisão" in message.model_text()


async def test_video_e_binario_viram_referencia_com_caminho(home: Path) -> None:
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
    message = await _build_user_vmessage(
        "veja",
        [
            _attachment(AttachmentKind.VIDEO, "demo.mp4", "video/mp4", mp4),
            _attachment(AttachmentKind.FILE, "dump.bin", "", b"\x00\x01\x02"),
        ],
        "thread-ref",
        user_id="u1",
        workspace_id="w1",
    )
    video, binary = message.content[1], message.content[2]
    assert video.media_kind == "video"
    assert video.text is not None and "analyze_video" in video.text
    assert str(home / "artifacts" / "thread-ref" / "media") in video.text
    assert binary.media_kind == "file"
    assert binary.asset_id is not None


async def test_anexo_media_chega_ao_provider_como_texto_e_nao_aparece_no_texto_do_usuario(
    home: Path,
) -> None:
    message = await _build_user_vmessage(
        "explique",
        [_attachment(AttachmentKind.CODE, "a.py", "text/x-python", b"x = 1\n")],
        "thread-text",
    )
    assert message.text() == "explique"
    sent = hydrate_media_blocks(
        [message], user_id="local", workspace_id="", thread_id="thread-text"
    )
    from backend.llm.media_parts import prepare_content

    prepared = prepare_content(sent[0].content, "anthropic")
    assert [block.kind for block in prepared] == ["text", "text"]
    assert "x = 1" in (prepared[1].text or "")


def test_historico_recarregado_lista_anexos_media() -> None:
    from backend.services.agent_factory import _history_pairs_from_messages
    from backend.vtypes.message import ContentBlock, MessageRole, VMessage

    message = VMessage(
        role=MessageRole.USER,
        content=[
            ContentBlock(kind="text", text="resuma"),
            ContentBlock(
                kind="media",
                media_kind="document",
                text="[Documento: r.pdf]\nconteúdo",
                asset_id="abc",
                attachment_name="r.pdf",
                attachment_mime_type="application/pdf",
                attachment_size_bytes=42,
            ),
        ],
    )
    [(role, text, _id, attachments)] = _history_pairs_from_messages(
        "t1", [(7, message)]
    )
    assert (role, text) == ("human", "resuma")
    assert attachments == [
        {
            "kind": "document",
            "name": "r.pdf",
            "mimeType": "application/pdf",
            "size": 42,
            "url": "/threads/t1/assets/abc",
            "asset_id": "abc",
        }
    ]
