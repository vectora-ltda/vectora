"""Geração e análise de vídeo pelo provider ativo.

Mesma regra das outras tools de mídia: nunca trocar de provider sozinho.
Duas coisas específicas de vídeo que estes testes travam:

- **Geração é assíncrona de verdade** (minutos). O polling tem teto: sem
  ele, o turno fica esperando para sempre quando o job nunca conclui —
  exatamente o modo de falha do incidente do NATS.
- **Analisar vídeo não é o mesmo que analisar imagem.** OpenAI e Anthropic
  estão em `VISION_CAPABLE_PROVIDERS` e não aceitam vídeo como entrada;
  reaproveitar aquela lista prometeria uma capacidade que a API recusa.

Cada caminho feliz tem o par de erro/borda no mesmo teste.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services.media_quota import media_quota
from backend.settings import provider_supports, settings
from backend.tools import media
from backend.tools.context import ToolContext


def _ctx(model: str) -> ToolContext:
    return ToolContext(
        model=model, thread_id="t-video", user_id=f"test-video-{uuid4().hex}"
    )


@pytest.fixture(autouse=True)
def _database_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inicializa o schema oficial para as reservas de quota dos testes."""
    database = tmp_path / "vectora.sqlite3"
    schema = (
        Path(__file__).parents[2]
        / "backend"
        / "storage"
        / "migrations"
        / "sqlite"
        / "schema.sql"
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))
    monkeypatch.setattr(settings, "db_file", database)
    monkeypatch.setattr(media_quota, "database", database)


# ---------------------------------------------------------------------------
# Matriz de capacidade
# ---------------------------------------------------------------------------


def test_capacidade_video_so_existe_onde_ha_geracao_de_verdade():
    # Happy: Veo é do Gemini.
    assert provider_supports("google-genai", "video") is True

    # Erro/borda: provider que faz imagem mas não vídeo continua `False` —
    # herdar a capacidade de "faz mídia" chamaria um endpoint inexistente.
    assert provider_supports("openai", "video") is False
    assert provider_supports("anthropic", "video") is False
    assert provider_supports("cohere", "video") is False


def test_capacidade_video_em_gateway_depende_do_modelo_configurado(monkeypatch):
    from backend.settings import settings

    # Erro/borda primeiro: sem modelo escolhido, a capacidade não existe.
    monkeypatch.setattr(settings, "openrouter_video_model", None, raising=False)
    assert provider_supports("openrouter", "video") is False

    monkeypatch.setattr(
        settings, "openrouter_video_model", "algum/modelo-video", raising=False
    )
    assert provider_supports("openrouter", "video") is True


def test_analise_de_video_e_mais_restrita_que_visao():
    """Erro/borda que motiva a constante separada: Anthropic e OpenAI leem
    imagem, não vídeo."""
    from backend.settings import VIDEO_INPUT_PROVIDERS, VISION_CAPABLE_PROVIDERS

    assert "google-genai" in VIDEO_INPUT_PROVIDERS
    assert VIDEO_INPUT_PROVIDERS < VISION_CAPABLE_PROVIDERS
    assert "anthropic" not in VIDEO_INPUT_PROVIDERS
    assert "openai" not in VIDEO_INPUT_PROVIDERS


# ---------------------------------------------------------------------------
# generate_video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_video_provider_sem_suporte_avisa_sem_chamar_sdk(monkeypatch):
    chamou = {"sdk": False}

    async def _nunca(*_a, **_k):
        chamou["sdk"] = True
        raise AssertionError("SDK não deveria ser chamado")

    monkeypatch.setattr(media, "_generate_video_bytes", _nunca)

    saida = json.loads(
        await media.generate_video(
            ctx=_ctx("cohere:command-a"), prompt="um gato de chapéu"
        )
    )

    assert "error" in saida
    assert "vídeo" in saida["error"]
    assert chamou["sdk"] is False


@pytest.mark.asyncio
async def test_generate_video_grava_mp4_e_prompt_vazio_e_recusado(
    monkeypatch, tmp_path
):
    async def _fake(*_a, **_k):
        return b"\x00\x00\x00\x18ftypmp42"

    monkeypatch.setattr(media, "_generate_video_bytes", _fake)
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    saida = json.loads(
        await media.generate_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"), prompt="um gato de chapéu"
        )
    )

    caminho = Path(saida["path"])
    assert caminho.suffix == ".mp4"
    assert caminho.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")
    # Fica na mesma pasta `media/` que a listagem de artifacts varre — sem
    # isso o arquivo existe em disco e não aparece em lugar nenhum da UI.
    assert caminho.parent == tmp_path / "media"

    # Erro/borda: prompt vazio nunca chega ao provider (geração de vídeo é
    # a chamada mais cara do produto).
    vazio = json.loads(
        await media.generate_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"), prompt="   "
        )
    )
    assert "error" in vazio


@pytest.mark.asyncio
async def test_generate_video_vazio_nao_grava_arquivo_de_zero_byte(
    monkeypatch, tmp_path
):
    """Erro/borda: gravar 0 byte criaria um artifact que a UI mostra como
    vídeo e nenhum player consegue abrir."""

    async def _vazio(*_a, **_k):
        return b""

    monkeypatch.setattr(media, "_generate_video_bytes", _vazio)
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    saida = json.loads(
        await media.generate_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"), prompt="um gato"
        )
    )

    assert "error" in saida
    assert not (tmp_path / "media").exists()


@pytest.mark.asyncio
async def test_generate_video_timeout_vira_erro_legivel(monkeypatch, tmp_path):
    """O job pode continuar rodando no provider — a mensagem precisa dizer
    isso, senão o usuário regera (e paga) achando que falhou."""

    async def _estourou(*_a, **_k):
        raise media.VideoGenerationTimeoutError(
            "geração não concluiu em 900s — o job pode seguir rodando"
        )

    monkeypatch.setattr(media, "_generate_video_bytes", _estourou)
    monkeypatch.setattr(media, "_media_dir", lambda _s: tmp_path / "media")

    saida = json.loads(
        await media.generate_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"), prompt="um gato"
        )
    )

    assert "error" in saida
    assert "seguir rodando" in saida["error"]
    assert not (tmp_path / "media").exists()


# ---------------------------------------------------------------------------
# Polling do Veo — o teto de tempo é o ponto
# ---------------------------------------------------------------------------


class _OperacaoFake:
    def __init__(self, *, done_apos: int) -> None:
        self.done = done_apos == 0
        self._restam = done_apos
        self.response = _RespostaFake()

    def avanca(self) -> _OperacaoFake:
        self._restam -= 1
        self.done = self._restam <= 0
        return self


class _VideoFake:
    video_bytes = b"\x00\x00\x00\x18ftypisom"


class _RespostaFake:
    generated_videos = [type("G", (), {"video": _VideoFake()})()]


class _ClienteVeoFake:
    """Só o que `_gemini_video_bytes` usa do SDK."""

    def __init__(self, *, done_apos: int) -> None:
        self.operacao = _OperacaoFake(done_apos=done_apos)
        self.consultas = 0

        cliente = self

        class _Models:
            async def generate_videos(self, **_kw):
                return cliente.operacao

        class _Ops:
            async def get(self, _op):
                cliente.consultas += 1
                return cliente.operacao.avanca()

        class _Aio:
            models = _Models()
            operations = _Ops()

        self.aio = _Aio()


@pytest.mark.asyncio
async def test_polling_do_veo_devolve_bytes_quando_conclui():
    cliente = _ClienteVeoFake(done_apos=2)

    data = await media._gemini_video_bytes(
        cliente, model="veo", prompt="gato", poll_interval_s=0, timeout_s=30
    )

    assert data.startswith(b"\x00\x00\x00\x18ftyp")
    assert cliente.consultas == 2


@pytest.mark.asyncio
async def test_polling_do_veo_respeita_o_teto_em_vez_de_girar_pra_sempre():
    """Erro/borda: job que nunca conclui. Sem teto o turno trava — o
    sintoma aparece como travamento, não como erro."""
    cliente = _ClienteVeoFake(done_apos=10_000)

    with pytest.raises(media.VideoGenerationTimeoutError, match="seguir rodando"):
        await media._gemini_video_bytes(
            cliente, model="veo", prompt="gato", poll_interval_s=0, timeout_s=0
        )


# ---------------------------------------------------------------------------
# analyze_video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_video_provider_sem_entrada_de_video_avisa(monkeypatch, tmp_path):
    """Anthropic passa em `VISION_CAPABLE_PROVIDERS` — este teste é o que
    impede a análise de vídeo de herdar aquela lista por engano."""
    chamou = {"sdk": False}

    async def _nunca(*_a, **_k):
        chamou["sdk"] = True
        raise AssertionError("SDK não deveria ser chamado")

    monkeypatch.setattr(media, "_analyze_video_text", _nunca)
    arquivo = tmp_path / "v.mp4"
    arquivo.write_bytes(b"conteudo")

    saida = json.loads(
        await media.analyze_video(
            ctx=_ctx("anthropic:claude-sonnet-4-5"),
            path=str(arquivo),
            question="o que acontece?",
        )
    )

    assert "error" in saida
    assert chamou["sdk"] is False


@pytest.mark.asyncio
async def test_analyze_video_responde_e_arquivo_inexistente_e_recusado(
    monkeypatch, tmp_path
):
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)

    async def _fake(_provider, _model, path, question):
        return f"vi {Path(path).name} e você perguntou: {question}"

    monkeypatch.setattr(media, "_analyze_video_text", _fake)
    arquivo = tmp_path / "artifacts" / "t-video" / "media" / "v.mp4"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(b"conteudo")

    saida = json.loads(
        await media.analyze_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"),
            path=str(arquivo),
            question="o que acontece?",
        )
    )
    assert "v.mp4" in saida["answer"]

    # Erro/borda: caminho que não existe não pode virar upload de nada.
    faltando = json.loads(
        await media.analyze_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"),
            path=str(tmp_path / "nao-existe.mp4"),
            question="?",
        )
    )
    assert "error" in faltando


@pytest.mark.asyncio
async def test_analyze_video_falha_do_sdk_vira_erro_tipado(monkeypatch, tmp_path):
    """Tool nunca propaga exceção — vira observação pro LLM."""
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)

    async def _explode(*_a, **_k):
        raise RuntimeError("quota estourada")

    monkeypatch.setattr(media, "_analyze_video_text", _explode)
    arquivo = tmp_path / "artifacts" / "t-video" / "media" / "v.mp4"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(b"conteudo")

    saida = json.loads(
        await media.analyze_video(
            ctx=_ctx("google-genai:gemini-2.5-pro"),
            path=str(arquivo),
            question="?",
        )
    )

    assert "quota estourada" in saida["error"]
