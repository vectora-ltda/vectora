"""Vectora Connect — tradução de formato nativo e ciclo de vida dos adapters.

Os adapters só traduzem: quem resolve thread e roda o agente é
`connect/runner.py`. O que estes testes travam é justamente o que a tradução
precisa **recusar** — mensagem do próprio bot, update sem texto, email sem
remetente — porque cada um desses casos, se passasse, viraria um turno de
conversa fantasma (ou um laço infinito de auto-resposta).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.connect import discord, email, manager, slack, telegram

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def test_telegram_traduz_texto_e_recusa_update_sem_conversa():
    update = SimpleNamespace(
        message=SimpleNamespace(text="olá", caption=None, chat=SimpleNamespace(id=42))
    )
    incoming = telegram.to_incoming(update)
    assert incoming is not None
    assert incoming.platform == "telegram"
    assert incoming.platform_user_id == "42"
    assert incoming.text == "olá"

    # Erro/borda: sticker (sem texto nem legenda) e update sem `message` — o
    # Telegram manda muitos updates que não são turno de conversa; tratá-los
    # como mensagem vazia acordaria o agente à toa.
    sem_texto = SimpleNamespace(
        message=SimpleNamespace(text=None, caption=None, chat=SimpleNamespace(id=42))
    )
    assert telegram.to_incoming(sem_texto) is None
    assert telegram.to_incoming(SimpleNamespace(message=None)) is None
    so_espaco = SimpleNamespace(
        message=SimpleNamespace(text="   ", caption=None, chat=SimpleNamespace(id=1))
    )
    assert telegram.to_incoming(so_espaco) is None


@pytest.mark.asyncio
async def test_telegram_handler_responde_e_nao_propaga_excecao(monkeypatch):
    enviados: list[str] = []

    async def _reply(text: str) -> None:
        enviados.append(text)

    async def _fake_process(incoming):
        return SimpleNamespace(text=f"eco: {incoming.text}")

    monkeypatch.setattr(telegram, "process_incoming", _fake_process)
    update = SimpleNamespace(
        message=SimpleNamespace(
            text="oi", caption=None, chat=SimpleNamespace(id=7), reply_text=_reply
        )
    )
    await telegram.handle_update(update)
    assert enviados == ["eco: oi"]

    # Erro/borda: falha no processamento não pode derrubar o loop de polling —
    # a integração ficaria muda até o próximo boot.
    async def _explode(_incoming):
        raise RuntimeError("agente fora do ar")

    monkeypatch.setattr(telegram, "process_incoming", _explode)
    await telegram.handle_update(update)
    assert enviados == ["eco: oi"]


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def test_discord_ignora_a_propria_mensagem_e_aceita_a_do_usuario():
    do_usuario = SimpleNamespace(
        author=SimpleNamespace(id=1, bot=False),
        content="oi",
        channel=SimpleNamespace(id=99),
    )
    incoming = discord.to_incoming(do_usuario, bot_user_id=555)
    assert incoming is not None
    assert incoming.platform_user_id == "99"

    # Erro/borda crítico: sem esse corte o bot responderia à própria resposta
    # em laço infinito.
    do_bot = SimpleNamespace(
        author=SimpleNamespace(id=555, bot=True),
        content="minha resposta",
        channel=SimpleNamespace(id=99),
    )
    assert discord.to_incoming(do_bot, bot_user_id=555) is None
    # Mesmo sem a flag `bot`, o id do próprio client basta pra cortar.
    do_bot_sem_flag = SimpleNamespace(
        author=SimpleNamespace(id=555, bot=False),
        content="minha resposta",
        channel=SimpleNamespace(id=99),
    )
    assert discord.to_incoming(do_bot_sem_flag, bot_user_id=555) is None
    # Anexo puro não é turno de conversa.
    anexo = SimpleNamespace(
        author=SimpleNamespace(id=1, bot=False),
        content="",
        channel=SimpleNamespace(id=99),
    )
    assert discord.to_incoming(anexo, bot_user_id=555) is None


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


def test_slack_traduz_evento_e_recusa_eco_e_edicao():
    incoming = slack.to_incoming({"text": "oi", "channel": "C1", "user": "U1"})
    assert incoming is not None
    assert incoming.platform_user_id == "C1"

    # Erro/borda: `bot_id` é a própria resposta do app (laço); `subtype`
    # cobre edição/remoção, que não são mensagem nova.
    assert slack.to_incoming({"text": "eco", "channel": "C1", "bot_id": "B1"}) is None
    assert (
        slack.to_incoming(
            {"text": "editada", "channel": "C1", "subtype": "message_changed"}
        )
        is None
    )
    assert slack.to_incoming({"channel": "C1"}) is None
    assert slack.to_incoming({"text": "sem canal"}) is None
    assert slack.to_incoming("não é dict") is None  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_slack_start_exige_os_dois_tokens():
    # Erro/borda: com só um token o Socket Mode falharia na conexão com uma
    # mensagem obscura — melhor recusar explicando qual token falta.
    with pytest.raises(ValueError, match="xapp-"):
        await slack.start("xoxb-algo", "")
    with pytest.raises(ValueError, match="xoxb-"):
        await slack.start("", "xapp-algo")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def test_email_traduz_assunto_e_corpo_e_recusa_sem_remetente():
    raw = b"From: Bruno <bruno@example.com>\r\nSubject: Relatorio\r\n\r\nManda o resumo\r\n"
    incoming = email.to_incoming(raw)
    assert incoming is not None
    assert incoming.platform_user_id == "bruno@example.com"
    assert "Relatorio" in incoming.text
    assert "Manda o resumo" in incoming.text

    # Erro/borda: sem remetente identificável não há pra onde responder.
    assert email.to_incoming(b"Subject: Sem remetente\r\n\r\ncorpo\r\n") is None
    # Sem assunto e sem corpo em texto (ex.: email só-HTML vazio).
    assert email.to_incoming(b"From: a@b.com\r\n\r\n") is None


@pytest.mark.asyncio
async def test_email_poll_once_continua_apos_falha_numa_mensagem(monkeypatch):
    config = email.EmailConfig(
        imap_host="imap.example.com",
        imap_user="eu@example.com",
        imap_password="senha",
        smtp_host="smtp.example.com",
    )
    raws = [
        b"From: a@example.com\r\nSubject: Um\r\n\r\ncorpo um\r\n",
        b"From: b@example.com\r\nSubject: Dois\r\n\r\ncorpo dois\r\n",
    ]
    monkeypatch.setattr(email, "fetch_unseen", lambda _c: raws)

    async def _process(incoming):
        return SimpleNamespace(text=f"resposta para {incoming.platform_user_id}")

    monkeypatch.setattr(email, "process_incoming", _process)

    enviados: list[str] = []

    def _send(_config, to_address, text):
        # A primeira falha; a segunda precisa ser respondida mesmo assim —
        # um email problemático não pode travar a caixa inteira.
        if to_address == "a@example.com":
            raise RuntimeError("SMTP recusou")
        enviados.append(f"{to_address}:{text}")

    monkeypatch.setattr(email, "send_reply", _send)

    respondidas = await email.poll_once(config)

    assert respondidas == 1
    assert enviados == ["b@example.com:resposta para b@example.com"]


@pytest.mark.asyncio
async def test_email_caixa_inacessivel_nao_derruba_o_loop(monkeypatch):
    config = email.EmailConfig(
        imap_host="imap.invalido",
        imap_user="eu@example.com",
        imap_password="senha",
        smtp_host="smtp.invalido",
    )

    def _explode(_c):
        raise OSError("conexão recusada")

    monkeypatch.setattr(email, "fetch_unseen", _explode)

    assert await email.poll_once(config) == 0


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _manager_limpo():
    manager._running.clear()
    yield
    manager._running.clear()


def test_configured_platforms_exige_credencial_completa(monkeypatch):
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "EMAIL_IMAP_HOST",
        "EMAIL_IMAP_USER",
        "EMAIL_IMAP_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    assert manager.configured_platforms() == set()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    assert manager.configured_platforms() == {"telegram"}

    # Erro/borda: Slack com só um dos dois tokens não conta como configurado —
    # subir assim daria erro de conexão sem o usuário entender o que faltou.
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    assert "slack" not in manager.configured_platforms()
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1")
    assert "slack" in manager.configured_platforms()

    # Email exige host + user + senha; faltando um, não sobe.
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_IMAP_USER", "eu@example.com")
    assert "email" not in manager.configured_platforms()
    monkeypatch.setenv("EMAIL_IMAP_PASSWORD", "senha")
    assert "email" in manager.configured_platforms()

    # Token em branco conta como ausente (campo limpo na UI).
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    assert "telegram" not in manager.configured_platforms()


def test_configured_platforms_vazio_sem_tier_pro(monkeypatch):
    """Sem Pro, nenhuma plataforma sobe mesmo com credencial completa salva."""
    for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "EMAIL_IMAP_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
    monkeypatch.setattr("backend.rbac.subscription.read_cached_status", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "abc")

    assert manager.configured_platforms() == set()

    # Erro/borda: com Pro (bypass), a mesma credencial já salva volta a contar.
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    assert manager.configured_platforms() == {"telegram", "discord"}


@pytest.mark.asyncio
async def test_sync_adapters_desliga_tudo_no_downgrade_de_tier(monkeypatch):
    """Downgrade pro->free: sync_adapters reconcilia contra vazio e desliga."""
    for var in (
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "EMAIL_IMAP_HOST",
        "EMAIL_IMAP_USER",
        "EMAIL_IMAP_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    async def _start(platform):
        return f"handle-{platform}"

    parados: list[str] = []

    async def _stop(platform, _handle):
        parados.append(platform)

    monkeypatch.setattr(manager, "_start_platform", _start)
    monkeypatch.setattr(manager, "_stop_platform", _stop)

    await manager.sync_adapters()
    assert manager.running_platforms() == {"telegram"}

    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "0")
    monkeypatch.setattr("backend.rbac.subscription.read_cached_status", lambda: None)
    status = await manager.sync_adapters()
    assert status["telegram"] == "stopped"
    assert manager.running_platforms() == set()
    assert parados == ["telegram"]


# ---------------------------------------------------------------------------
# Manager — toggle enabled/disabled (desacoplado da credencial)
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_runtime_settings(tmp_path, monkeypatch):
    """Isola cada teste com um RuntimeSettings próprio — sem isso, o
    override de `connect_enabled_platforms` de um teste vazaria pros
    seguintes (é um singleton de processo)."""
    from backend.workspace import runtime_settings as rs_module
    from backend.workspace.runtime_settings import RuntimeSettings

    fresh = RuntimeSettings(path=tmp_path / "settings.json")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)
    return fresh


def test_is_enabled_antes_do_primeiro_toggle_espelha_credencial(
    _isolated_runtime_settings, monkeypatch
):
    """Achado ao vivo (2026-08-20): antes desta feature, credencial salva
    já ligava a plataforma pra sempre. Sem nenhum toggle usado ainda, o
    comportamento tem que continuar idêntico — só muda quando o usuário
    de fato usa o toggle."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert manager.is_enabled("discord") is False

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    assert manager.is_enabled("discord") is True


def test_configured_platforms_respeita_toggle_desligado(
    _isolated_runtime_settings, monkeypatch
):
    """O caso do bug real: credencial presente, mas o usuário desligou o
    toggle — a plataforma não pode aparecer como configurada."""
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    assert "discord" in manager.configured_platforms()

    manager.set_enabled("discord", False)
    assert "discord" not in manager.configured_platforms()

    # Erro/borda: religar sem credencial não bota a plataforma de volta.
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    manager.set_enabled("discord", True)
    assert "discord" not in manager.configured_platforms()


def test_primeiro_toggle_preserva_outras_plataformas_ja_credenciadas(
    _isolated_runtime_settings, monkeypatch
):
    """Desligar UMA plataforma pela primeira vez não pode desligar
    silenciosamente outra que já estava rodando de propósito."""
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

    manager.set_enabled("discord", False)

    assert manager.is_enabled("discord") is False
    assert manager.is_enabled("telegram") is True
    assert manager.configured_platforms() == {"telegram"}


@pytest.mark.asyncio
async def test_sync_adapters_falha_de_um_nao_impede_os_outros(monkeypatch):
    monkeypatch.setattr(
        manager, "configured_platforms", lambda: {"telegram", "discord"}
    )

    async def _start(platform):
        if platform == "telegram":
            raise RuntimeError("token inválido")
        return f"handle-{platform}"

    monkeypatch.setattr(manager, "_start_platform", _start)

    status = await manager.sync_adapters()

    # O adapter quebrado reporta a falha, o outro sobe normalmente — nem o
    # boot do backend nem as demais plataformas dependem dele.
    assert status["telegram"].startswith("failed:")
    assert status["discord"] == "started"
    assert manager.running_platforms() == {"discord"}


@pytest.mark.asyncio
async def test_sync_adapters_desliga_quem_perdeu_a_credencial(monkeypatch):
    parados: list[str] = []

    async def _stop(platform, _handle):
        parados.append(platform)

    async def _start(platform):
        return f"handle-{platform}"

    monkeypatch.setattr(manager, "_stop_platform", _stop)
    monkeypatch.setattr(manager, "_start_platform", _start)

    def _so_telegram():
        return {"telegram"}

    monkeypatch.setattr(manager, "configured_platforms", _so_telegram)
    await manager.sync_adapters()
    assert manager.running_platforms() == {"telegram"}

    # Credencial removida nas Settings -> adapter desligado no próximo sync.
    def _nada_configurado():
        return set()

    monkeypatch.setattr(manager, "configured_platforms", _nada_configurado)
    status = await manager.sync_adapters()
    assert status["telegram"] == "stopped"
    assert manager.running_platforms() == set()

    # Idempotente: sincronizar de novo com nada configurado não tenta parar
    # de novo nem levanta.
    assert await manager.sync_adapters() == {}
    assert parados == ["telegram"]


@pytest.mark.asyncio
async def test_stop_all_e_idempotente(monkeypatch):
    async def _stop(_platform, _handle):
        return None

    monkeypatch.setattr(manager, "_stop_platform", _stop)
    manager._running["telegram"] = object()

    await manager.stop_all()
    assert manager.running_platforms() == set()
    await manager.stop_all()
    assert manager.running_platforms() == set()


@pytest.mark.asyncio
async def test_start_platform_rejeita_plataforma_desconhecida():
    with pytest.raises(ValueError, match="desconhecida"):
        await manager._start_platform("orkut")


# ---------------------------------------------------------------------------
# Store — mapeamento (plataforma, usuário) -> thread
# ---------------------------------------------------------------------------


@pytest.fixture
def _db_isolado(tmp_path, monkeypatch):
    from backend.settings import settings

    monkeypatch.setattr(settings, "db_dsn", str(tmp_path / "connect.db"))


@pytest.mark.asyncio
async def test_mapeamento_reusa_o_mesmo_thread_e_isola_por_plataforma(_db_isolado):
    from backend.services.connect import store

    # Primeira mensagem: não existe mapeamento ainda.
    assert await store.lookup_thread("telegram", "42") is None

    thread = await store.create_thread_mapping("telegram", "42")
    assert thread.startswith("connect-telegram-")
    assert await store.lookup_thread("telegram", "42") == thread

    # Erro/borda 1: chamar de novo (duas mensagens quase simultâneas) não pode
    # criar um segundo thread e perder o histórico.
    assert await store.create_thread_mapping("telegram", "42") == thread

    # Erro/borda 2: mesmo id em outra plataforma é outra pessoa — thread
    # próprio, senão conversas de gente diferente se misturariam.
    outro = await store.create_thread_mapping("discord", "42")
    assert outro != thread
    assert await store.lookup_thread("discord", "42") == outro
