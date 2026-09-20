"""GatewayClient (backend/services/gateway/__init__.py)"""

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from backend.services.gateway import GatewayMessage, GatewayRequestItem

if TYPE_CHECKING:
    from backend.services.gateway import GatewayClient


@pytest.fixture
def gateway_token_file(tmp_path: Path) -> Path:
    return tmp_path / "gateway_token"


@pytest.fixture
def gateway_secret_file(tmp_path: Path) -> Path:
    return tmp_path / "gateway_connector_secret"


@pytest.fixture
def gateway_url() -> str:
    return "wss://gateway.vectora.chat"


class TestGatewayTokenPersistence:
    def test_carrega_token_existente(self, gateway_token_file: Path) -> None:
        gateway_token_file.write_text("abc123")
        from backend.services.gateway.token import load_token

        assert load_token(gateway_token_file) == "abc123"

    def test_retorna_none_sem_arquivo(self, gateway_token_file: Path) -> None:
        from backend.services.gateway.token import load_token

        assert load_token(gateway_token_file) is None

    def test_salva_token(self, gateway_token_file: Path) -> None:
        from backend.services.gateway.token import save_token

        save_token("xyz789", gateway_token_file)
        assert gateway_token_file.read_text() == "xyz789"

    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="Windows usa ACLs NTFS; chmod POSIX não aplica",
    )
    def test_token_salvo_tem_permissao_restrita(self, gateway_token_file: Path) -> None:
        import stat

        from backend.services.gateway.token import save_token

        save_token("xyz789", gateway_token_file)
        mode = gateway_token_file.stat().st_mode
        # arquivo não deve ser legível por outros (world)
        assert not (mode & stat.S_IROTH)


class TestGatewayClientBackoff:
    @pytest.mark.asyncio
    async def test_backoff_dobra_a_cada_falha(self) -> None:
        """Jitter neutralizado (`random.uniform` fixo em 1.0) pra testar só
        a duplicação do backoff-base, sem a variação aleatória do sleep
        real — essa variação tem teste próprio abaixo."""
        from backend.services.gateway import GatewayClient

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        delays: list[float] = []

        async def fake_sleep(d: float) -> None:
            delays.append(d)
            if len(delays) >= 3:
                raise asyncio.CancelledError

        with patch("backend.services.gateway.asyncio.sleep", fake_sleep):
            with patch("backend.services.gateway.random.uniform", return_value=1.0):
                with patch(
                    "backend.services.gateway.GatewayClient._connect_once",
                    side_effect=ConnectionError("fail"),
                ):
                    with patch(
                        "backend.services.gateway.GatewayClient._register",
                        return_value=("tok123", "sec123"),
                    ):
                        with contextlib.suppress(asyncio.CancelledError):
                            await client._connect_loop()

        assert len(delays) >= 2
        assert delays[1] == delays[0] * 2

    @pytest.mark.asyncio
    async def test_backoff_nao_ultrapassa_60s(self) -> None:
        from backend.services.gateway import GatewayClient

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        delays: list[float] = []

        async def fake_sleep(d: float) -> None:
            delays.append(d)
            if len(delays) >= 10:
                raise asyncio.CancelledError

        with patch("backend.services.gateway.asyncio.sleep", fake_sleep):
            with patch("backend.services.gateway.random.uniform", return_value=1.0):
                with patch(
                    "backend.services.gateway.GatewayClient._connect_once",
                    side_effect=ConnectionError("fail"),
                ):
                    with patch(
                        "backend.services.gateway.GatewayClient._register",
                        return_value=("tok123", "sec123"),
                    ):
                        with contextlib.suppress(asyncio.CancelledError):
                            await client._connect_loop()

        assert all(d <= 60.0 for d in delays)
        assert max(delays) == 60.0

    @pytest.mark.asyncio
    async def test_jitter_faz_delays_variarem_mesmo_com_backoff_estavel(self) -> None:
        """Sem neutralizar `random.uniform` (jitter real): depois que o
        backoff-base satura em 60s (bem antes da 15ª tentativa: 1,2,4,...,
        60), os últimos delays vêm todos do MESMO backoff-base — só variam
        se o jitter estiver de fato sendo aplicado no sleep. Prova a defesa
        contra thundering herd (várias instalações reconectando ao mesmo
        tempo depois de o Worker do gateway reiniciar)."""
        from backend.services.gateway import GatewayClient

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        delays: list[float] = []

        async def fake_sleep(d: float) -> None:
            delays.append(d)
            if len(delays) >= 15:
                raise asyncio.CancelledError

        with patch("backend.services.gateway.asyncio.sleep", fake_sleep):
            with patch(
                "backend.services.gateway.GatewayClient._connect_once",
                side_effect=ConnectionError("fail"),
            ):
                with patch(
                    "backend.services.gateway.GatewayClient._register",
                    return_value=("tok123", "sec123"),
                ):
                    with contextlib.suppress(asyncio.CancelledError):
                        await client._connect_loop()

        stabilized = delays[-5:]
        assert len(set(stabilized)) > 1, "delays no teto deveriam variar (jitter real)"
        assert all(30.0 <= d <= 90.0 for d in stabilized)


class TestGatewayClientRegister:
    @pytest.mark.asyncio
    async def test_register_usa_app_secret_e_fingerprint(
        self, gateway_token_file: Path, gateway_secret_file: Path
    ) -> None:
        """O handshake de registro autentica com o secret fixo do produto
        (embutido no build), não mais um JWT por-instalação — o corpo carrega
        só o fingerprint da máquina. A resposta também traz o connector_secret
        (único fator que autoriza abrir o WebSocket como dono da sessão —
        ver services/src/gateway/gateway-session.ts), persistido à parte."""
        from backend.services.gateway import GatewayClient

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"token": "abc123", "connector_secret": "sec-abc123"}
        )

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="fixed-product-secret",
            token_path=gateway_token_file,
            secret_path=gateway_secret_file,
            fingerprint="fp-machine-a",
        )

        with patch(
            "backend.services.gateway.aiohttp.ClientSession"
        ) as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response),
                    __aexit__=AsyncMock(return_value=None),
                )
            )
            mock_session_cls.return_value = mock_session

            token, secret = await client._register()

        assert token == "abc123"
        assert secret == "sec-abc123"
        assert gateway_token_file.read_text() == "abc123"
        assert gateway_secret_file.read_text() == "sec-abc123"
        _, call_kwargs = mock_session.post.call_args
        assert call_kwargs["headers"]["Authorization"] == "Bearer fixed-product-secret"
        assert call_kwargs["json"] == {"fingerprint": "fp-machine-a"}

    @pytest.mark.asyncio
    async def test_duas_instalacoes_mesmo_secret_fingerprints_diferentes(
        self, tmp_path: Path
    ) -> None:
        """Erro/borda: o esquema antigo (JWT assinado por instalação) nunca
        batia contra um secret único no Worker — este teste prova que o novo
        esquema autentica corretamente duas instalações distintas usando o
        MESMO VECTORA_APP_SECRET, cada uma com seu próprio fingerprint."""
        from backend.services.gateway import GatewayClient

        for fp, expected_token in (("fp-a", "tok-a"), ("fp-b", "tok-b")):
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(
                return_value={
                    "token": expected_token,
                    "connector_secret": f"sec-{expected_token}",
                }
            )

            client = GatewayClient(
                gateway_url="wss://gateway.vectora.chat",
                app_secret="shared-product-secret",
                token_path=tmp_path / f"gateway_token_{fp}",
                secret_path=tmp_path / f"gateway_secret_{fp}",
                fingerprint=fp,
            )

            with patch(
                "backend.services.gateway.aiohttp.ClientSession"
            ) as mock_session_cls:
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_session.post = MagicMock(
                    return_value=AsyncMock(
                        __aenter__=AsyncMock(return_value=mock_response),
                        __aexit__=AsyncMock(return_value=None),
                    )
                )
                mock_session_cls.return_value = mock_session

                token, secret = await client._register()

            assert token == expected_token
            assert secret == f"sec-{expected_token}"
            _, call_kwargs = mock_session.post.call_args
            assert (
                call_kwargs["headers"]["Authorization"]
                == "Bearer shared-product-secret"
            )

    @pytest.mark.asyncio
    async def test_register_reutiliza_token_e_secret_existentes(
        self, gateway_token_file: Path, gateway_secret_file: Path
    ) -> None:
        from backend.services.gateway import GatewayClient

        gateway_token_file.write_text("existing")
        gateway_secret_file.write_text("existing-secret")
        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
            token_path=gateway_token_file,
            secret_path=gateway_secret_file,
        )

        with patch(
            "backend.services.gateway.aiohttp.ClientSession"
        ) as mock_session_cls:
            token, secret = await client._register()

        assert token == "existing"
        assert secret == "existing-secret"
        mock_session_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_erro_borda_token_sem_secret_reregistra_migracao_transparente(
        self, gateway_token_file: Path, gateway_secret_file: Path
    ) -> None:
        """Migração de uma instalação de antes desta correção: só o token
        estava salvo localmente (secret nunca existiu). `_register` detecta
        a ausência do secret e chama a API de novo — como o token é
        determinístico por fingerprint, o `/register` novo devolve o MESMO
        subdomínio (nenhuma URL de callback OAuth já configurada pelo
        usuário quebra), só o connector_secret é novo."""
        from backend.services.gateway import GatewayClient

        gateway_token_file.write_text("existing")
        assert not gateway_secret_file.exists()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"token": "existing", "connector_secret": "novo-secret"}
        )

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
            token_path=gateway_token_file,
            secret_path=gateway_secret_file,
            fingerprint="fp-migracao",
        )

        with patch(
            "backend.services.gateway.aiohttp.ClientSession"
        ) as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_response),
                    __aexit__=AsyncMock(return_value=None),
                )
            )
            mock_session_cls.return_value = mock_session

            token, secret = await client._register()

        assert token == "existing"
        assert secret == "novo-secret"
        assert gateway_secret_file.read_text() == "novo-secret"
        mock_session_cls.assert_called_once()


class TestGatewayClientStop:
    @pytest.mark.asyncio
    async def test_stop_cancela_task(self) -> None:
        from backend.services.gateway import GatewayClient

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        client._task = mock_task

        await client.stop()

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_idempotente_sem_task(self) -> None:
        from backend.services.gateway import GatewayClient

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        await client.stop()  # não deve lançar erro


class _AsyncCtx:
    """Async context manager mínimo — envolve um valor síncrono, mesmo
    padrão de `aiohttp.ClientSession()`/`session.ws_connect()`."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info) -> None:
        return None


class TestGatewayClientConnectOnce:
    def _client(self):
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )

    @pytest.mark.asyncio
    async def test_abre_websocket_com_o_connector_secret_no_header(self) -> None:
        """O secret é o único fator que autoriza abrir a conexão como dono
        da sessão (ver gateway-session.ts::handleWebSocketUpgrade) — sem
        mandar `Authorization: Bearer <secret>` no handshake do próprio
        WS, o Worker rejeita com 401."""
        client = self._client()
        ws = AsyncMock()
        session = MagicMock()
        session.ws_connect = MagicMock(return_value=_AsyncCtx(ws))

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            return_value=_AsyncCtx(session),
        ):
            with patch.object(client, "_handle_messages", new=AsyncMock()):
                await client._connect_once("tok123", "meu-secret-de-conector")

        call_args: tuple[object, ...]
        call_kwargs: dict[str, object]
        call_args, call_kwargs = session.ws_connect.call_args
        assert call_args[0] == "wss://gateway.vectora.chat/ws/tok123"
        assert call_kwargs["headers"] == {
            "Authorization": "Bearer meu-secret-de-conector"
        }

    @pytest.mark.asyncio
    async def test_fechamento_limpo_do_servidor_loga_warning_antes_de_reconectar(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`_handle_messages` retornando sem lançar significa que o servidor
        fechou o socket sem frame de erro — sem log nenhum, isso reconecta
        em silêncio (várias linhas "conectado" seguidas, sem nenhum
        "desconectado" no meio), dificultando diagnosticar se o padrão
        coincide com outros sintomas."""
        client = self._client()
        ws = AsyncMock()
        session = MagicMock()
        session.ws_connect = MagicMock(return_value=_AsyncCtx(ws))

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            return_value=_AsyncCtx(session),
        ):
            with patch.object(client, "_handle_messages", new=AsyncMock()):
                with caplog.at_level("WARNING", logger="backend.services.gateway"):
                    await client._connect_once("tok123", "sec123")

        assert any(
            "fechada pelo servidor sem erro" in rec.message for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_conexao_com_erro_nao_loga_o_warning_de_fechamento_limpo(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Erro/borda: quando `_handle_messages` lança (ws error de verdade,
        já reportado por `_connect_loop` como "desconectado"), o novo
        warning de fechamento limpo não deve duplicar o log."""
        client = self._client()
        ws = AsyncMock()
        session = MagicMock()
        session.ws_connect = MagicMock(return_value=_AsyncCtx(ws))

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            return_value=_AsyncCtx(session),
        ):
            with patch.object(
                client,
                "_handle_messages",
                new=AsyncMock(side_effect=ConnectionError("ws error: boom")),
            ):
                with caplog.at_level("WARNING", logger="backend.services.gateway"):
                    with pytest.raises(ConnectionError):
                        await client._connect_once("tok123", "sec123")

        assert not any(
            "fechada pelo servidor sem erro" in rec.message for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_local_session_tem_timeout_explicito_nao_o_default_de_5min(
        self,
    ) -> None:
        """Sem `timeout=` explícito, `local_session` usaria o
        `ClientTimeout(total=300)` default do aiohttp — como `_connect_once`
        aguarda `pending` no `finally` antes de reconectar, um handler local
        travado atrasaria a reconexão em até 5 minutos."""
        from backend.services.gateway import _LOCAL_FORWARD_TIMEOUT_S

        client = self._client()
        ws = AsyncMock()
        session = MagicMock()
        session.ws_connect = MagicMock(return_value=_AsyncCtx(ws))

        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def session_factory(*args: object, **kwargs: object) -> _AsyncCtx:
            calls.append((args, kwargs))
            value = session if len(calls) == 1 else MagicMock()
            return _AsyncCtx(value)

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            side_effect=session_factory,
        ):
            with patch.object(client, "_handle_messages", new=AsyncMock()):
                await client._connect_once("tok123", "sec123")

        assert len(calls) == 2
        _, local_session_kwargs = calls[1]
        timeout = local_session_kwargs.get("timeout")
        assert isinstance(timeout, aiohttp.ClientTimeout)
        total = timeout.total
        assert total is not None
        assert total == _LOCAL_FORWARD_TIMEOUT_S
        assert total < 300

    @pytest.mark.asyncio
    async def test_reusa_a_mesma_local_session_entre_varios_forwards(self) -> None:
        """`local_session` é aberta UMA vez em `_connect_once` e reusada por
        todos os `_forward` da conexão — antes, cada `_forward` abria a
        própria `aiohttp.ClientSession()` (2 requests processadas = 3
        sessões: 1 do WS + 2 do forward; agora são só 2: 1 do WS + 1
        reusada)."""
        client = self._client()

        class _FakeMsg:
            def __init__(self, tp, data=None) -> None:
                self.type = tp
                self._data = data

            def json(self):
                return self._data

        class _FakeWS:
            def __init__(self, messages) -> None:
                self._messages = list(messages)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._messages:
                    raise StopAsyncIteration
                return self._messages.pop(0)

            async def send_json(self, _data) -> None:
                return None

        ws = _FakeWS(
            [
                _FakeMsg(
                    aiohttp.WSMsgType.TEXT,
                    {
                        "type": "request",
                        "id": "r1",
                        "method": "GET",
                        "path": "/a",
                        "headers": {},
                        "body": "",
                    },
                ),
                _FakeMsg(
                    aiohttp.WSMsgType.TEXT,
                    {
                        "type": "request",
                        "id": "r2",
                        "method": "GET",
                        "path": "/b",
                        "headers": {},
                        "body": "",
                    },
                ),
            ]
        )

        ws_owner_session = MagicMock()
        ws_owner_session.ws_connect = MagicMock(return_value=_AsyncCtx(ws))
        local_session_marker = MagicMock()

        created: list[object] = []

        def session_factory(*_args, **_kwargs):
            value = ws_owner_session if len(created) == 0 else local_session_marker
            created.append(value)
            return _AsyncCtx(value)

        sessions_seen: list[object] = []

        async def fake_forward(_ws, session_arg, req) -> None:
            sessions_seen.append(session_arg)

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            side_effect=session_factory,
        ):
            with patch.object(client, "_forward", side_effect=fake_forward):
                await client._connect_once("tok123", "sec123")

        assert len(created) == 2, "1 sessão pro WS + 1 reusada — não 1 por forward"
        assert sessions_seen == [local_session_marker, local_session_marker]


class TestGatewayClientDispatch:
    def _client(self):
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )

    def _queue(self):
        from backend.services.gateway import _MAX_CONCURRENT_FORWARDS

        return asyncio.Queue(maxsize=_MAX_CONCURRENT_FORWARDS)

    @pytest.mark.asyncio
    async def test_ping_envia_pong(self) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()
        await client._dispatch(ws, session, {"type": "ping"}, queue)
        ws.send_json.assert_awaited_once_with({"type": "pong"})
        assert queue.qsize() == 0  # ping nunca passa pela fila

    @pytest.mark.asyncio
    async def test_queued_enfileira_todos_itens_sem_rodar_forward(self) -> None:
        """`_dispatch` só ENFILEIRA (`queue.put`) — não roda `_forward` nem
        cria task nenhuma. Quem consome a fila são os workers fixos
        (`_forward_worker`, testado à parte); sem nenhum worker rodando
        aqui, os itens ficam parados na fila intactos."""
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()
        items: list[GatewayRequestItem] = [
            {"id": "1", "method": "POST", "path": "/w/a", "headers": {}, "body": ""},
            {"id": "2", "method": "POST", "path": "/w/b", "headers": {}, "body": ""},
        ]
        message: GatewayMessage = {"type": "queued", "items": items}
        with patch.object(client, "_forward", new=AsyncMock()) as mock_fwd:
            await client._dispatch(ws, session, message, queue)
            assert queue.qsize() == 2
            mock_fwd.assert_not_called()

        job1 = queue.get_nowait()
        job2 = queue.get_nowait()
        assert job1 == (ws, session, items[0])
        assert job2 == (ws, session, items[1])

    @pytest.mark.asyncio
    async def test_request_enfileira_o_proprio_job(self) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()
        req: GatewayMessage = {
            "type": "request",
            "id": "abc",
            "method": "POST",
            "path": "/w/gh",
            "headers": {},
            "body": "",
        }
        await client._dispatch(ws, session, req, queue)

        assert queue.qsize() == 1
        job = queue.get_nowait()
        assert job == (ws, session, req)

    @pytest.mark.asyncio
    async def test_tipo_desconhecido_ignorado(self) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()
        await client._dispatch(
            ws, session, {"type": "unknown_msg"}, queue
        )  # sem exceção
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_erro_borda_fila_cheia_bloqueia_dispatch_ate_um_slot_liberar(
        self,
    ) -> None:
        """Backpressure real: `queue.put` (dentro de `_dispatch`) bloqueia
        quando a fila está no teto — isso pausa a LEITURA de novas
        mensagens do WebSocket (`_handle_messages` chama `await
        self._dispatch(...)`), em vez de crescer sem limite como as tasks
        soltas de antes."""
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)

        first: GatewayMessage = {
            "type": "request",
            "id": "1",
            "method": "GET",
            "path": "/a",
            "headers": {},
            "body": "",
        }
        second: GatewayMessage = {
            "type": "request",
            "id": "2",
            "method": "GET",
            "path": "/b",
            "headers": {},
            "body": "",
        }
        await client._dispatch(ws, session, first, queue)  # enche a fila (maxsize=1)

        dispatch_second = asyncio.create_task(
            client._dispatch(ws, session, second, queue)
        )
        for _ in range(50):
            if dispatch_second.done():
                break
            await asyncio.sleep(0)
        assert not dispatch_second.done(), "dispatch deveria bloquear com fila cheia"

        queue.get_nowait()  # libera 1 slot, como um worker faria
        await asyncio.wait_for(dispatch_second, timeout=1.0)
        assert queue.qsize() == 1


class TestGatewayClientReviewJob:
    def _client(self) -> "GatewayClient":
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )

    def _queue(self) -> "asyncio.Queue[object]":
        from backend.services.gateway import _MAX_CONCURRENT_FORWARDS

        return asyncio.Queue(maxsize=_MAX_CONCURRENT_FORWARDS)

    @pytest.mark.asyncio
    async def test_dispatch_de_review_job_nao_passa_pela_fila_de_forwards(self) -> None:
        """review_job usa a fila limitada própria, sem ocupar forwards."""
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()

        with patch.object(client, "_handle_review_job", new=AsyncMock()) as mock_handle:
            await client._dispatch(
                ws,
                session,
                {
                    "type": "review_job",
                    "job_id": "job-1",
                    "diff": "diff x",
                    "metadata": {"pr": "1"},
                    "callback_secret": "secret-do-job",
                },
                queue,
            )
            await client._review_queue.join()

        assert queue.qsize() == 0
        mock_handle.assert_awaited_once_with(
            "job-1", "diff x", {"pr": "1"}, "secret-do-job"
        )
        await client.stop()

    @pytest.mark.asyncio
    async def test_dispatch_de_review_job_descarta_delivery_duplicado(self) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()
        message: GatewayMessage = {
            "type": "review_job",
            "job_id": "job-1",
            "diff": "diff x",
            "metadata": {},
            "callback_secret": "secret-do-job",
            "delivery_id": "delivery-1",
        }

        with patch.object(client, "_handle_review_job", new=AsyncMock()) as mock_handle:
            await client._dispatch(ws, session, message, queue)
            await client._dispatch(ws, session, message, queue)
            await client._review_queue.join()

        mock_handle.assert_awaited_once()
        await client.stop()

    @pytest.mark.asyncio
    async def test_dispatch_de_review_job_invalido_falha_job_persistido(self) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue = self._queue()

        with patch.object(client, "_post_review_result", new=AsyncMock()) as mock_post:
            await client._dispatch(
                ws,
                session,
                cast(
                    "GatewayMessage",
                    {
                        "type": "review_job",
                        "job_id": "job-invalid",
                        "diff": "diff x",
                        "metadata": {},
                        "callback_secret": "secret-do-job",
                        "head_sha": "malformed-sha",
                    },
                ),
                queue,
            )
            await asyncio.sleep(0)

        mock_post.assert_awaited_once()
        assert mock_post.await_args is not None
        assert mock_post.await_args.args[:2] == ("job-invalid", "secret-do-job")
        assert "invalid review_job payload" in mock_post.await_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_handle_review_job_sucesso_posta_review_text(self) -> None:
        client = self._client()

        with patch(
            "backend.services.gateway.review_job.run_review_job",
            new=AsyncMock(return_value="LGTM"),
        ):
            with patch.object(
                client, "_post_review_result", new=AsyncMock()
            ) as mock_post:
                await client._handle_review_job("job-1", "diff x", {}, "secret-do-job")

        mock_post.assert_awaited_once_with("job-1", "secret-do-job", review_text="LGTM")

    @pytest.mark.asyncio
    async def test_erro_borda_handle_review_job_falha_posta_error_em_vez_de_propagar(
        self,
    ) -> None:
        """Um erro rodando a revisão não pode derrubar a task solta em
        silêncio — vira um `error` postado de volta, pra Action saber que o
        job falhou em vez de ficar em `pending` pra sempre."""
        client = self._client()

        with patch(
            "backend.services.gateway.review_job.run_review_job",
            new=AsyncMock(side_effect=ConnectionError("provider indisponível")),
        ):
            with patch.object(
                client, "_post_review_result", new=AsyncMock()
            ) as mock_post:
                await client._handle_review_job("job-1", "diff x", {}, "secret-do-job")

        mock_post.assert_awaited_once_with(
            "job-1", "secret-do-job", error="provider indisponível"
        )

    @pytest.mark.asyncio
    async def test_post_review_result_manda_pro_endpoint_certo(self) -> None:
        client = self._client()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            return_value=mock_session,
        ) as session_cls:
            await client._post_review_result(
                "job-1", "secret-do-job", review_text="LGTM"
            )

        call_args, call_kwargs = mock_session.post.call_args
        assert call_args[0] == (
            "https://services.vectora.company/gha-bot/review/job-1/result"
        )
        assert call_kwargs["json"] == {"review_text": "LGTM"}
        assert call_kwargs["headers"] == {"Authorization": "Bearer secret-do-job"}
        assert session_cls.call_args.kwargs["timeout"].total == 10

    @pytest.mark.asyncio
    async def test_stop_sinaliza_reviews_que_aguardavam_na_fila(self) -> None:
        client = self._client()
        await client._review_queue.put(("queued-1", "diff", {}, "secret"))

        with patch.object(client, "_post_review_result", new=AsyncMock()) as mock_post:
            await client.stop()

        mock_post.assert_awaited_once_with(
            "queued-1", "secret", error="review worker shutting down; retry later"
        )

    @pytest.mark.asyncio
    async def test_erro_borda_post_review_result_falha_de_rede_nao_propaga(
        self,
    ) -> None:
        """Se o POST em si falhar (provider offline, DNS, etc.), não pode
        derrubar a task solta — só loga; o job fica `pending` no D1 até a
        Action desistir do long-poll, o que é aceitável (não há pra onde
        mais reportar o erro nesse cenário)."""
        client = self._client()

        with patch(
            "backend.services.gateway.aiohttp.ClientSession",
            side_effect=ConnectionError("sem rede"),
        ):
            await client._post_review_result(
                "job-1", "secret-do-job", error="x"
            )  # não lança


class TestGatewayClientForward:
    def _client(self):
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
            local_url="http://localhost:8000",
        )

    @pytest.mark.asyncio
    async def test_forward_sucesso_envia_response(self) -> None:
        """`_forward` recebe a sessão local já pronta (reuso — ver
        `_connect_once`), não abre/fecha uma `aiohttp.ClientSession()`
        própria a cada chamada."""
        import base64

        client = self._client()
        ws = AsyncMock()

        resp_body = b'{"ok": true}'
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read = AsyncMock(return_value=resp_body)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=mock_resp)

        req: GatewayRequestItem = {
            "id": "req-1",
            "method": "POST",
            "path": "/webhook/github",
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"ref":"main"}').decode(),
        }
        await client._forward(ws, mock_session, req)

        call_kwargs = ws.send_json.call_args[0][0]
        assert call_kwargs["type"] == "response"
        assert call_kwargs["id"] == "req-1"
        assert call_kwargs["status"] == 200
        assert base64.b64decode(call_kwargs["body"]) == resp_body

    @pytest.mark.asyncio
    async def test_forward_erro_de_rede_envia_502(self) -> None:
        client = self._client()
        ws = AsyncMock()
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=ConnectionError("down"))

        await client._forward(
            ws,
            mock_session,
            {
                "id": "req-2",
                "method": "GET",
                "path": "/health",
                "headers": {},
                "body": "",
            },
        )

        call_kwargs = ws.send_json.call_args[0][0]
        assert call_kwargs["status"] == 502
        assert call_kwargs["id"] == "req-2"

    @pytest.mark.asyncio
    async def test_erro_de_rede_nao_loga_headers_nem_body(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Erro/borda: `headers`/`body` podem carregar segredos (token
        `Authorization`, assinatura de webhook, `code` de callback OAuth) —
        o log de erro só pode conter method/path, nunca o request inteiro."""
        client = self._client()
        ws = AsyncMock()
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=ConnectionError("down"))

        with caplog.at_level("ERROR", logger="backend.services.gateway"):
            await client._forward(
                ws,
                mock_session,
                {
                    "id": "req-secret",
                    "method": "POST",
                    "path": "/webhook/github",
                    "headers": {"Authorization": "Bearer super-secret-token"},
                    "body": "codigo_oauth_sigiloso",
                },
            )

        record = next(
            r for r in caplog.records if "erro ao encaminhar request" in r.message
        )
        assert getattr(record, "method", None) == "POST"
        assert getattr(record, "path", None) == "/webhook/github"
        assert not hasattr(record, "headers")
        assert not hasattr(record, "body")
        assert not hasattr(record, "req")
        rendered = record.getMessage()
        assert "super-secret-token" not in rendered
        assert "codigo_oauth_sigiloso" not in rendered

    @pytest.mark.asyncio
    async def test_erro_borda_cancelamento_repropaga_sem_enviar_response(self) -> None:
        """`asyncio.CancelledError` (conexão fechando, `_connect_once`
        cancelando `pending`) precisa se propagar — não pode ser tratado
        como erro genérico e mascarado por um 502 enviado num socket que já
        pode estar fechando."""
        client = self._client()
        ws = AsyncMock()
        mock_session = MagicMock()
        mock_session.request = MagicMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await client._forward(
                ws,
                mock_session,
                {
                    "id": "req-3",
                    "method": "GET",
                    "path": "/x",
                    "headers": {},
                    "body": "",
                },
            )
        ws.send_json.assert_not_awaited()


class TestGatewayClientForwardWorker:
    def _client(self):
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )

    @pytest.mark.asyncio
    async def test_worker_processa_um_job_e_continua_esperando_o_proximo(
        self,
    ) -> None:
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue: asyncio.Queue = asyncio.Queue()

        req: GatewayRequestItem = {
            "id": "1",
            "method": "GET",
            "path": "/a",
            "headers": {},
            "body": "",
        }
        await queue.put((ws, session, req))

        with patch.object(client, "_forward", new=AsyncMock()) as mock_fwd:
            worker = asyncio.create_task(client._forward_worker(queue))
            await queue.join()  # espera o worker consumir o único item
            mock_fwd.assert_awaited_once_with(ws, session, req)

            assert not worker.done(), "worker deve seguir vivo, esperando o próximo"

            from backend.services.gateway import _STOP_WORKER

            await queue.put(_STOP_WORKER)
            await asyncio.wait_for(worker, timeout=1.0)

    @pytest.mark.asyncio
    async def test_erro_borda_stop_worker_nao_chama_forward(self) -> None:
        client = self._client()
        queue: asyncio.Queue = asyncio.Queue()

        from backend.services.gateway import _STOP_WORKER

        await queue.put(_STOP_WORKER)
        with patch.object(client, "_forward", new=AsyncMock()) as mock_fwd:
            await asyncio.wait_for(client._forward_worker(queue), timeout=1.0)
        mock_fwd.assert_not_called()

    @pytest.mark.asyncio
    async def test_erro_borda_job_que_falha_nao_mata_o_worker(self) -> None:
        """Se o WebSocket já estiver fechando, até o `ws.send_json` de
        dentro do `except` de `_forward` pode lançar (`ConnectionReset
        Error` do aiohttp) — sem capturar isso dentro do worker, ele
        morreria de vez. Com menos workers vivos, o dreno gracioso em
        `_connect_once` (um `_STOP_WORKER` por worker) ficaria esperando
        um worker que nunca mais lê a fila, travando a reconexão."""
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue: asyncio.Queue = asyncio.Queue()

        bad_req: GatewayRequestItem = {
            "id": "bad",
            "method": "GET",
            "path": "/a",
            "headers": {},
            "body": "",
        }
        good_req: GatewayRequestItem = {
            "id": "good",
            "method": "GET",
            "path": "/b",
            "headers": {},
            "body": "",
        }
        await queue.put((ws, session, bad_req))
        await queue.put((ws, session, good_req))

        processed: list[str] = []

        async def fake_forward(_ws, _session, req: GatewayRequestItem) -> None:
            if req["id"] == "bad":
                raise ConnectionResetError("socket já fechando")
            processed.append(req["id"])

        with patch.object(client, "_forward", side_effect=fake_forward):
            worker = asyncio.create_task(client._forward_worker(queue))
            await queue.join()  # espera os 2 itens serem consumidos

            assert not worker.done(), "worker sobrevive ao job que falhou"
            assert processed == ["good"]  # o job bom, depois do ruim, rodou

            from backend.services.gateway import _STOP_WORKER

            await queue.put(_STOP_WORKER)
            await asyncio.wait_for(worker, timeout=1.0)


class TestGatewayClientConcurrency:
    def _client(self):
        from backend.services.gateway import GatewayClient

        return GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )

    @pytest.mark.asyncio
    async def test_segunda_request_nao_espera_a_primeira_lenta_terminar(self) -> None:
        """Bug real corrigido: `_dispatch` fazia `await self._forward(...)`
        direto — uma revisão de PR demorada bloqueava o loop de leitura do
        WebSocket inteiro, incl. um simples ping. Agora `_dispatch` só
        enfileira; workers fixos (`_forward_worker`) processam em paralelo
        — a segunda request termina mesmo com a primeira ainda presa,
        desde que haja mais de 1 worker (o cenário real: `_connect_once`
        sempre sobe `_MAX_CONCURRENT_FORWARDS` deles)."""
        client = self._client()
        ws = AsyncMock()
        session = AsyncMock()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)

        started: list[str] = []
        finished: list[str] = []
        release_slow = asyncio.Event()

        async def fake_forward(ws_arg, session_arg, req) -> None:
            assert session_arg is session  # mesma sessão reusada nas duas
            started.append(req["id"])
            if req["id"] == "slow":
                await release_slow.wait()
            finished.append(req["id"])

        with patch.object(client, "_forward", side_effect=fake_forward):
            workers = [
                asyncio.create_task(client._forward_worker(queue)) for _ in range(2)
            ]

            await client._dispatch(
                ws,
                session,
                {
                    "type": "request",
                    "id": "slow",
                    "method": "GET",
                    "path": "/a",
                    "headers": {},
                    "body": "",
                },
                queue,
            )
            await client._dispatch(
                ws,
                session,
                {
                    "type": "request",
                    "id": "fast",
                    "method": "GET",
                    "path": "/b",
                    "headers": {},
                    "body": "",
                },
                queue,
            )

            for _ in range(100):
                if "fast" in finished:
                    break
                await asyncio.sleep(0)

            assert "fast" in finished
            assert "slow" not in finished  # ainda preso em release_slow.wait()

            release_slow.set()
            from backend.services.gateway import _STOP_WORKER

            for _ in workers:
                await queue.put(_STOP_WORKER)
            await asyncio.gather(*workers)

        assert set(finished) == {"slow", "fast"}
        assert started == ["slow", "fast"]  # ordem de chegada preservada

    @pytest.mark.asyncio
    async def test_no_maximo_max_concurrent_forwards_workers_processam_ao_mesmo_tempo(
        self,
    ) -> None:
        """Sem limite, um `queued` grande (ou o Worker mandando `request`
        mais rápido do que o backend local responde) faz forwards em voo
        crescerem sem teto — com `_MAX_CONCURRENT_FORWARDS` workers fixos
        consumindo uma fila do mesmo tamanho, nunca mais que esse número
        de requests HTTP locais roda ao mesmo tempo, e a fila em si nunca
        cresce além do teto (o `put` de itens extras bloqueia)."""
        from backend.services.gateway import (
            _MAX_CONCURRENT_FORWARDS,
            _STOP_WORKER,
            GatewayClient,
        )

        client = GatewayClient(
            gateway_url="wss://gateway.vectora.chat",
            app_secret="test-app-secret",
        )
        ws = AsyncMock()

        in_flight = 0
        max_in_flight = 0
        release = asyncio.Event()

        class _FakeResp:
            status = 200
            headers: dict[str, str] = {}

            async def __aenter__(self) -> "_FakeResp":
                nonlocal in_flight, max_in_flight
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await release.wait()
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                nonlocal in_flight
                in_flight -= 1

            async def read(self) -> bytes:
                return b""

        def response_factory(**_kwargs: object) -> _FakeResp:
            return _FakeResp()

        session = MagicMock()
        session.request = MagicMock(side_effect=response_factory)

        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_CONCURRENT_FORWARDS)
        workers = [
            asyncio.create_task(client._forward_worker(queue))
            for _ in range(_MAX_CONCURRENT_FORWARDS)
        ]

        total_requests = _MAX_CONCURRENT_FORWARDS + 5

        async def enqueue_all() -> None:
            for i in range(total_requests):
                req: GatewayRequestItem = {
                    "id": str(i),
                    "method": "GET",
                    "path": "/x",
                    "headers": {},
                    "body": "",
                }
                # put() bloqueia sozinho quando a fila enche — não precisa
                # de nenhum controle explícito de backpressure aqui.
                await queue.put((ws, session, req))

        enqueue_task = asyncio.create_task(enqueue_all())

        for _ in range(200):
            if in_flight >= _MAX_CONCURRENT_FORWARDS:
                break
            await asyncio.sleep(0)

        assert in_flight == _MAX_CONCURRENT_FORWARDS
        # A fila só aceita mais _MAX_CONCURRENT_FORWARDS itens (5 dos 25
        # totais) além dos que já viraram forwards em voo — o resto do
        # `enqueue_all` está bloqueado em `queue.put`, não acumulado.
        assert not enqueue_task.done()

        release.set()
        await asyncio.wait_for(enqueue_task, timeout=2.0)
        for _ in workers:
            await queue.put(_STOP_WORKER)
        await asyncio.gather(*workers)

        assert max_in_flight == _MAX_CONCURRENT_FORWARDS


class TestMachineFingerprint:
    def test_retorna_string_hex_de_32_chars(self) -> None:
        from backend.services.gateway import _machine_fingerprint

        fp = _machine_fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 32
        assert fp.isalnum()

    def test_usa_machine_id_linux(self, tmp_path: Path) -> None:
        import sys

        if sys.platform == "win32":
            pytest.skip("Linux-only path test")
        from backend.services.gateway import _machine_fingerprint

        machine_id_file = tmp_path / "machine-id"
        machine_id_file.write_text("abc123def456\n")

        with patch("backend.services.gateway.Path") as mock_path_cls:
            mock_path_cls.side_effect = lambda *args: (
                machine_id_file if args[0] == "/etc/machine-id" else Path(*args)
            )
            fp = _machine_fingerprint()

        assert isinstance(fp, str)
        assert len(fp) == 32

    def test_usa_hostname_como_fallback(self) -> None:
        import sys

        from backend.services.gateway import _machine_fingerprint

        if sys.platform == "win32":
            with patch("winreg.OpenKey", side_effect=OSError("no registry")):
                fp = _machine_fingerprint()
        else:
            with patch("backend.services.gateway.Path") as mock_path_cls:
                mock_p = MagicMock()
                mock_p.is_file.return_value = False
                mock_path_cls.return_value = mock_p
                fp = _machine_fingerprint()

        assert isinstance(fp, str)
        assert len(fp) == 32
