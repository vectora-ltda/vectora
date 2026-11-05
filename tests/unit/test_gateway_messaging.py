"""Vectora Connect — camada de abstração de mensageria.

resolve_thread_id: reusa thread existente por platform_user_id, cria na
primeira mensagem. handle_incoming_message: fecha o loop e nunca deixa o
usuário externo sem resposta, mesmo com falha do agente.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.gateway.messaging import (
    IncomingMessage,
    handle_incoming_message,
    resolve_thread_id,
)


def _incoming(
    platform="telegram", platform_user_id="123", text="oi"
) -> IncomingMessage:
    return IncomingMessage(
        platform=platform, platform_user_id=platform_user_id, text=text
    )


@pytest.mark.asyncio
async def test_resolve_thread_id_reuses_existing_mapping():
    async def lookup(platform, uid):
        assert (platform, uid) == ("telegram", "123")
        return "thread-existing"

    async def create(platform, uid):
        raise AssertionError("não deveria criar — já existe mapeamento")

    thread_id = await resolve_thread_id(_incoming(), lookup=lookup, create=create)

    assert thread_id == "thread-existing"


@pytest.mark.asyncio
async def test_resolve_thread_id_creates_new_thread_on_first_message():
    async def lookup(platform, uid):
        return None

    async def create(platform, uid):
        return "thread-new"

    thread_id = await resolve_thread_id(_incoming(), lookup=lookup, create=create)

    assert thread_id == "thread-new"


@pytest.mark.asyncio
async def test_handle_incoming_message_returns_agent_reply():
    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        raise AssertionError("não deveria criar")

    async def run_agent(thread_id, text):
        assert thread_id == "thread-1"
        assert text == "oi"
        return "resposta do agente"

    result = await handle_incoming_message(
        _incoming(), lookup=lookup, create=create, run_agent=run_agent
    )

    assert result.platform == "telegram"
    assert result.platform_user_id == "123"
    assert result.text == "resposta do agente"


@pytest.mark.asyncio
async def test_handle_incoming_message_agent_failure_returns_friendly_error_not_exception():
    # Erro/borda: agente indisponível não pode deixar o usuário externo sem
    # resposta nenhuma (tools defensivas, CLAUDE.md regra 11).
    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        return "thread-1"

    async def run_agent(thread_id, text):
        raise RuntimeError("LLM indisponível")

    result = await handle_incoming_message(
        _incoming(), lookup=lookup, create=create, run_agent=run_agent
    )

    assert result.text
    assert "não consegui" in result.text.lower()


@pytest.mark.asyncio
async def test_resolve_thread_id_string_vazia_e_tratada_como_existente_nao_none():
    # Borda: thread_id == "" é falsy em contexto booleano, mas o código
    # checa `is not None` explicitamente — "" conta como "já existe".
    async def lookup(platform, uid):
        return ""

    async def create(platform, uid):
        raise AssertionError("não deveria criar — lookup devolveu string vazia")

    thread_id = await resolve_thread_id(_incoming(), lookup=lookup, create=create)

    assert thread_id == ""


@pytest.mark.asyncio
async def test_resolve_thread_id_propaga_excecao_do_lookup_sem_mascarar():
    # resolve_thread_id em si não é a camada defensiva (é
    # handle_incoming_message que captura) — confirma que uma falha de
    # storage no lookup propaga normalmente pra quem chamar direto.
    async def lookup(platform, uid):
        raise ConnectionError("banco de mapeamento indisponível")

    async def create(platform, uid):
        raise AssertionError("não deveria chegar aqui")

    with pytest.raises(ConnectionError, match="indisponível"):
        await resolve_thread_id(_incoming(), lookup=lookup, create=create)


@pytest.mark.asyncio
async def test_resolve_thread_id_propaga_excecao_do_create():
    async def lookup(platform, uid):
        return None

    async def create(platform, uid):
        raise RuntimeError("falha ao gravar mapeamento novo")

    with pytest.raises(RuntimeError, match="falha ao gravar"):
        await resolve_thread_id(_incoming(), lookup=lookup, create=create)


@pytest.mark.asyncio
async def test_handle_incoming_message_falha_no_lookup_tambem_gera_resposta_amigavel():
    # A falha defensiva cobre TODO o fluxo (resolve_thread_id + run_agent),
    # não só o run_agent — usuário externo nunca fica sem resposta mesmo
    # se o storage de mapeamento cair.
    async def lookup(platform, uid):
        raise ConnectionError("banco indisponível")

    async def create(platform, uid):
        return "thread-x"

    async def run_agent(thread_id, text):
        raise AssertionError("não deveria chegar aqui — lookup já falhou")

    result = await handle_incoming_message(
        _incoming(), lookup=lookup, create=create, run_agent=run_agent
    )

    assert "não consegui" in result.text.lower()
    assert result.platform == "telegram"
    assert result.platform_user_id == "123"


@pytest.mark.asyncio
async def test_handle_incoming_message_com_texto_vazio_ainda_chama_o_agente():
    # Erro/borda: mensagem vazia (ex. usuário mandou só um sticker/foto sem
    # legenda) não é filtrada aqui — passa adiante pro agente decidir.
    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        raise AssertionError("não deveria criar")

    calls = []

    async def run_agent(thread_id, text):
        calls.append(text)
        return "recebi algo sem texto"

    result = await handle_incoming_message(
        _incoming(text=""), lookup=lookup, create=create, run_agent=run_agent
    )

    assert calls == [""]
    assert result.text == "recebi algo sem texto"


@pytest.mark.asyncio
async def test_handle_incoming_message_resposta_vazia_do_agente_e_preservada():
    # Borda: run_agent pode legitimamente devolver string vazia (ex. agente
    # decidiu não responder nada) — handler não substitui por mensagem
    # padrão, só o caminho de exceção tem fallback amigável.
    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        raise AssertionError("não deveria criar")

    async def run_agent(thread_id, text):
        return ""

    result = await handle_incoming_message(
        _incoming(), lookup=lookup, create=create, run_agent=run_agent
    )

    assert result.text == ""


def test_incoming_message_sem_attachments_usa_tupla_vazia_default():
    incoming = _incoming()

    assert incoming.attachments == ()


def test_incoming_message_com_attachments_preserva_ordem():
    # Ordem trocada: attachments é tupla, ordem de entrada é preservada —
    # importa pra UI que renderiza anexos na sequência recebida.
    incoming = IncomingMessage(
        platform="telegram",
        platform_user_id="123",
        text="olha essas fotos",
        attachments=("foto2.jpg", "foto1.jpg"),
    )

    assert incoming.attachments == ("foto2.jpg", "foto1.jpg")


@pytest.mark.asyncio
async def test_resolucao_concorrente_de_dois_usuarios_diferentes_nao_colide():
    # Concorrência: duas mensagens de platform_user_id distintos resolvidas
    # ao mesmo tempo não podem trocar de thread_id entre si.
    mapping = {"111": "thread-111", "222": None}
    created = {}

    async def lookup(platform, uid):
        return mapping.get(uid)

    async def create(platform, uid):
        thread_id = f"thread-new-{uid}"
        created[uid] = thread_id
        return thread_id

    result_a, result_b = await asyncio.gather(
        resolve_thread_id(
            _incoming(platform_user_id="111"), lookup=lookup, create=create
        ),
        resolve_thread_id(
            _incoming(platform_user_id="222"), lookup=lookup, create=create
        ),
    )

    assert result_a == "thread-111"
    assert result_b == "thread-new-222"


@pytest.mark.asyncio
async def test_mesmo_platform_user_id_em_plataformas_diferentes_nao_compartilha_thread():
    # Duplicado aparente: "123" existe no Telegram, mas o mesmo ID literal
    # no Discord é uma pessoa diferente — lookup recebe (platform, uid), o
    # par completo é a chave, não só o uid.
    calls = []

    async def lookup(platform, uid):
        calls.append((platform, uid))
        return "thread-telegram-123" if platform == "telegram" else None

    async def create(platform, uid):
        return f"thread-{platform}-{uid}"

    result_telegram = await resolve_thread_id(
        _incoming(platform="telegram", platform_user_id="123"),
        lookup=lookup,
        create=create,
    )
    result_discord = await resolve_thread_id(
        _incoming(platform="discord", platform_user_id="123"),
        lookup=lookup,
        create=create,
    )

    assert result_telegram == "thread-telegram-123"
    assert result_discord == "thread-discord-123"
    assert result_telegram != result_discord


@pytest.mark.asyncio
async def test_handle_incoming_message_create_thread_failure_still_returns_friendly_error():
    # Erro/borda: falha ao CRIAR o mapeamento (não só ao rodar o agente)
    # também precisa do fallback amigável — usuário nunca vê exceção crua.
    async def lookup(platform, uid):
        return None

    async def create(platform, uid):
        raise RuntimeError("falha ao gravar mapeamento")

    async def run_agent(thread_id, text):
        raise AssertionError("não deveria chegar aqui — create já falhou")

    result = await handle_incoming_message(
        _incoming(), lookup=lookup, create=create, run_agent=run_agent
    )

    assert "não consegui" in result.text.lower()


@pytest.mark.asyncio
async def test_handle_incoming_message_com_attachments_preserva_no_retorno_via_incoming():
    # attachments não aparecem em OutgoingMessage (é um dataclass separado
    # sem esse campo) — confirma que o handler não tenta acessá-los e quebra.
    incoming = IncomingMessage(
        platform="telegram",
        platform_user_id="123",
        text="veja isso",
        attachments=("a.jpg", "b.jpg"),
    )

    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        raise AssertionError("não deveria criar")

    async def run_agent(thread_id, text):
        return "recebido"

    result = await handle_incoming_message(
        incoming, lookup=lookup, create=create, run_agent=run_agent
    )

    assert result.text == "recebido"


def test_outgoing_message_is_frozen_dataclass_immutable():
    from backend.services.gateway.messaging import OutgoingMessage

    msg = OutgoingMessage(platform="telegram", platform_user_id="1", text="oi")

    with pytest.raises(Exception):  # noqa: B017 — dataclass frozen levanta FrozenInstanceError
        msg.text = "outra coisa"  # ty: ignore[invalid-assignment]


@pytest.mark.asyncio
async def test_handle_incoming_message_texto_muito_longo_ainda_e_repassado_intacto():
    # Borda: sem truncamento no handler — quem decide limites de tamanho é
    # a camada de plataforma/run_agent, não este módulo de abstração.
    long_text = "a" * 10000

    async def lookup(platform, uid):
        return "thread-1"

    async def create(platform, uid):
        raise AssertionError("não deveria criar")

    captured = {}

    async def run_agent(thread_id, text):
        captured["len"] = len(text)
        return "ok"

    await handle_incoming_message(
        _incoming(text=long_text), lookup=lookup, create=create, run_agent=run_agent
    )

    assert captured["len"] == 10000
