"""Gateway client — mantém WebSocket persistente com gateway.vectora.chat.

Cada instância do vectora recebe um subdomínio único (ex:
x7k2m.vectora.chat). Webhooks e callbacks OAuth externos chegam pelo gateway
e são despachados ao FastAPI local sem abrir porta TCP pública.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import TypedDict, cast

import aiohttp

from backend.services.gateway.token import load_token, save_token

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_PATH = Path.home() / ".vectora" / "gateway_token"
#: Segredo de conector — único fator que autoriza abrir o WebSocket como
#: dono da sessão (o token público/subdomínio sozinho não basta mais, ver
#: `services/src/gateway/gateway-session.ts::handleWebSocketUpgrade`).
#: Arquivo separado do token (mesmo diretório) — se só o token existir
#: (instalação de antes desta correção), `_register` detecta a ausência e
#: re-registra pra obter um secret novo, sem trocar de subdomínio.
_DEFAULT_SECRET_PATH = Path.home() / ".vectora" / "gateway_connector_secret"
_REGISTER_PATH = "/register"
_WS_PATH = "/ws/"
#: Mesma convenção já usada em backend/services/license.py — auto-referência
#: hardcoded ao Worker services/, não uma env var configurável (é sempre o
#: mesmo endpoint fixo do produto).
_SERVICES_URL = "https://services.vectora.company"
_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0
#: Jitter aplicado ao sleep de reconexão (não ao estado interno do backoff
#: exponencial) — evita thundering herd quando o Worker do gateway reinicia
#: e todas as instâncias tentam reconectar no mesmo instante.
_BACKOFF_JITTER = (0.5, 1.5)
#: Teto do round-trip HTTP local (loopback, mesmo processo) — sem isso o
#: `ClientTimeout(total=300)` default do aiohttp valeria, e `_connect_once`
#: aguarda `pending` no reconnect: um handler local travado atrasaria a
#: reconexão em até 5 minutos.
_LOCAL_FORWARD_TIMEOUT_S = 30.0
#: Nº de workers fixos processando forwards + tamanho máximo da fila entre
#: eles e o loop de leitura do WebSocket. Um semáforo sozinho ao redor do
#: request HTTP não bastava: a *task* de cada forward já nascia fora do
#: teto (criada assim que a mensagem chegava), só o request HTTP em si
#: ficava represado — um `queued` grande ainda criava uma task por item,
#: sem limite, consumindo memória. Com fila limitada + workers fixos,
#: `_dispatch` (via `queue.put`) bloqueia a PRÓPRIA leitura de novas
#: mensagens do WebSocket quando a fila enche — backpressure real, não só
#: nos requests HTTP locais.
_MAX_CONCURRENT_FORWARDS = 20
#: Sentinela de "pare" — um por worker, enfileirado no fechamento da
#: conexão pra cada worker terminar o que já está processando/enfileirado
#: e sair, em vez de ficar bloqueado pra sempre em `queue.get()`.
_STOP_WORKER = object()


class GatewayRequestItem(TypedDict):
    """Item de `type: "request"` ou de dentro de `type: "queued"` — sempre
    tem `id`/`method`/`path`; `headers`/`body` seguem lidos via `.get()`
    (payload malformado do lado do Worker não pode derrubar o forward)."""

    id: str
    method: str
    path: str
    headers: dict[str, str]
    body: str


class GatewayMessage(TypedDict, total=False):
    """Mensagem do protocolo do túnel (ver `services/src/gateway/types.ts`,
    fonte de verdade do formato — este é só o espelho do lado Python).
    `total=False` porque cada `type` tem um subconjunto diferente de campos
    obrigatórios (`ping` só tem `type`, `request` tem id/method/path...)."""

    type: str
    id: str
    method: str
    path: str
    headers: dict[str, str]
    body: str
    items: list[GatewayRequestItem]
    # type == "review_job" (gh-bot self-hosted, ver review_job.py) — sem
    # `id` (não é um par request/response do túnel, ver docstring de
    # `GatewayMessage["review_job"]` em types.ts).
    job_id: str
    diff: str
    metadata: dict[str, str]
    callback_secret: str


#: Item real de trabalho na fila — carrega o `ws`/`local_session` da conexão
#: atual junto com o request, já que múltiplas conexões nunca compartilham
#: fila (uma fila nova por `_connect_once`).
_ForwardJob = tuple[
    "aiohttp.ClientWebSocketResponse", "aiohttp.ClientSession", "GatewayRequestItem"
]


class GatewayClient:
    """WebSocket client que mantém conexão com o gateway e repassa requests ao backend local."""

    def __init__(
        self,
        gateway_url: str,
        app_secret: str,
        local_url: str = "http://localhost:8000",
        token_path: Path = _DEFAULT_TOKEN_PATH,
        secret_path: Path = _DEFAULT_SECRET_PATH,
        fingerprint: str | None = None,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._http_base = self._gateway_url.replace("wss://", "https://").replace(
            "ws://", "http://"
        )
        self._app_secret = app_secret
        self._local_url = local_url.rstrip("/")
        self._token_path = token_path
        self._secret_path = secret_path
        self._fingerprint = fingerprint or _machine_fingerprint()
        self._task: asyncio.Task[None] | None = None
        #: Serializa `ws.send_json` — `_forward` roda concorrente (uma task
        #: por request em voo), e aiohttp não garante frames intactos se
        #: `send_json`/`send_str` forem chamados de tasks diferentes ao
        #: mesmo tempo na mesma conexão.
        self._ws_send_lock = asyncio.Lock()
        #: Referência forte às tasks de review_job em voo — sem isso, o
        #: garbage collector pode derrubar a task no meio (pegadinha
        #: conhecida do asyncio: `create_task` sem guardar a referência não
        #: garante a task viva até terminar). `add_done_callback` limpa
        #: sozinho quando a task acaba (sucesso ou erro).
        self._review_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia o loop de conexão em background (idempotente)."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._connect_loop(), name="gateway-client")

    async def stop(self) -> None:
        """Cancela o loop de conexão (idempotente)."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _register(self) -> tuple[str, str]:
        """Retorna (token, connector_secret) existentes ou registra no
        gateway e persiste — chama a API de novo (idempotente pro token,
        que é determinístico por fingerprint) sempre que o secret local
        estiver ausente, mesmo com um token já salvo: cobre tanto a
        primeira instalação quanto a migração transparente de uma
        instalação de antes desta correção (token salvo sem secret nenhum
        — o gateway aceitava WebSocket só com o token até então)."""
        existing_token = load_token(self._token_path)
        existing_secret = load_token(self._secret_path)
        if existing_token and existing_secret:
            return existing_token, existing_secret

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._http_base}{_REGISTER_PATH}",
                headers={"Authorization": f"Bearer {self._app_secret}"},
                json={"fingerprint": self._fingerprint},
            ) as resp:
                resp.raise_for_status()
                data: dict[str, str] = await resp.json()

        token: str = data["token"]
        secret: str = data["connector_secret"]
        save_token(token, self._token_path)
        save_token(secret, self._secret_path)
        logger.info("gateway: token registrado — %s.vectora.chat", token)
        return token, secret

    async def _connect_loop(self) -> None:
        """Loop infinito com backoff exponencial até cancelamento."""
        backoff = _BACKOFF_INITIAL
        token, secret = await self._register()

        while True:
            try:
                await self._connect_once(token, secret)
                backoff = _BACKOFF_INITIAL
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # jitter de timing (reconexão), não uso criptográfico
                sleep_for = backoff * random.uniform(  # noqa: S311  # nosec B311
                    *_BACKOFF_JITTER
                )
                logger.warning(
                    "gateway: desconectado (%s), retry em %.1fs", exc, sleep_for
                )
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _connect_once(self, token: str, secret: str) -> None:
        """Abre WebSocket, processa mensagens até fechar.

        Duas sessões aiohttp com propósitos distintos: `session` é dona do
        WebSocket com o gateway; `local_session` é reusada por TODOS os
        `_forward` desta conexão (fala com o FastAPI local) — antes cada
        `_forward` abria/fechava sua própria sessão, sem reuso de conexão.

        `_MAX_CONCURRENT_FORWARDS` workers fixos consomem uma fila
        limitada — nem a fila nem o nº de forwards em voo crescem sem
        teto, mesmo com um `queued` grande ou o Worker mandando `request`
        mais rápido do que o backend local responde.
        """
        ws_url = f"{self._gateway_url}{_WS_PATH}{token}"
        queue: asyncio.Queue[_ForwardJob | object] = asyncio.Queue(
            maxsize=_MAX_CONCURRENT_FORWARDS
        )
        workers = [
            asyncio.create_task(self._forward_worker(queue))
            for _ in range(_MAX_CONCURRENT_FORWARDS)
        ]
        local_timeout = aiohttp.ClientTimeout(total=_LOCAL_FORWARD_TIMEOUT_S)
        try:
            async with (
                aiohttp.ClientSession() as session,
                aiohttp.ClientSession(timeout=local_timeout) as local_session,
            ):
                try:
                    async with session.ws_connect(
                        ws_url, headers={"Authorization": f"Bearer {secret}"}
                    ) as ws:
                        logger.info("gateway: conectado em %s", ws_url)
                        await self._handle_messages(ws, local_session, queue)
                        # `_handle_messages` só retorna sem lançar quando o
                        # servidor fecha o socket sem frame de erro (`async
                        # for` simplesmente esgota). Sem este log, isso
                        # reconecta em silêncio — várias linhas "conectado"
                        # seguidas, sem nenhum "desconectado" no meio,
                        # dificultando diagnosticar se o padrão coincide com
                        # outros sintomas (ex.: o processo sendo derrubado
                        # logo depois de conectar).
                        logger.warning(
                            "gateway: conexão fechada pelo servidor sem erro — reconectando"
                        )
                finally:
                    # Sinaliza fim de fila — cada worker termina o que já
                    # está processando/enfileirado antes de sair (nenhum
                    # webhook/callback OAuth já aceito na fila é descartado
                    # silenciosamente só porque o WebSocket fechou por
                    # perto); `_LOCAL_FORWARD_TIMEOUT_S` garante que essa
                    # espera nunca é indefinida.
                    for _ in workers:
                        await queue.put(_STOP_WORKER)
                    await asyncio.gather(*workers, return_exceptions=True)
        except asyncio.CancelledError:
            # Cancelamento externo (`stop()`) não pode esperar o dreno
            # gracioso — cancela os workers direto.
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

    async def _handle_messages(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        local_session: aiohttp.ClientSession,
        queue: asyncio.Queue[_ForwardJob | object],
    ) -> None:
        """Recebe mensagens do gateway e despacha ao FastAPI local."""
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._dispatch(ws, local_session, msg.json(), queue)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"ws error: {ws.exception()}")

    async def _dispatch(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        local_session: aiohttp.ClientSession,
        message: GatewayMessage,
        queue: asyncio.Queue[_ForwardJob | object],
    ) -> None:
        """Enfileira `request`/itens de `queued` pros workers processarem.

        `queue.put` bloqueia quando a fila está cheia — isso pausa a
        LEITURA de novas mensagens do WebSocket (backpressure real), mas
        nunca um único `ping` por trás de um forward lento: ping é
        respondido direto aqui, sem passar pela fila.
        """
        kind = message.get("type")

        if kind == "ping":
            async with self._ws_send_lock:
                await ws.send_json({"type": "pong"})
            return

        if kind == "queued":
            for item in message.get("items", []):
                await queue.put((ws, local_session, item))
            return

        if kind == "request":
            # kind == "request" garante id/method/path presentes (contrato
            # do Worker, ver GatewayMessage em services/src/gateway/types.ts).
            await queue.put((ws, local_session, cast("GatewayRequestItem", message)))
            return

        if kind == "review_job":
            from backend.services.gateway.review_contract import ReviewJobRequest

            try:
                job = ReviewJobRequest.model_validate(message)
            except ValueError:
                logger.warning("gateway: review_job inválido descartado")
                return
            # Roda fora da fila de forwards (_MAX_CONCURRENT_FORWARDS é pra
            # requests HTTP rápidas; um review job real pode levar minutos —
            # ocupar um worker da fila até terminar atrasaria callbacks
            # OAuth/webhooks normais). Task solta, não bloqueia a leitura do
            # WebSocket (`_handle_messages` só chama `await self._dispatch`,
            # nunca espera o review terminar).
            task = asyncio.create_task(
                self._handle_review_job(
                    job.job_id,
                    job.diff,
                    job.metadata,
                    job.callback_secret,
                ),
                name=f"gha-review-{message.get('job_id', '')}",
            )
            self._review_tasks.add(task)
            task.add_done_callback(self._review_tasks.discard)
            return

    async def _handle_review_job(
        self, job_id: str, diff: str, metadata: dict[str, str], callback_secret: str
    ) -> None:
        """Roda a revisão de PR self-hosted e posta o resultado de volta —
        FORA do túnel (POST outbound normal, sem problema de NAT), já que o
        job pode levar minutos e o túnel é pra request/response síncrono."""
        from backend.services.gateway.review_job import run_review_job

        try:
            review_text = await run_review_job(diff, metadata)
            await self._post_review_result(
                job_id, callback_secret, review_text=review_text
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("gateway: review_job %s falhou", job_id)
            await self._post_review_result(job_id, callback_secret, error=str(exc))

    async def _post_review_result(
        self,
        job_id: str,
        callback_secret: str,
        *,
        review_text: str | None = None,
        error: str | None = None,
    ) -> None:
        body: dict[str, str] = {}
        if review_text is not None:
            body["review_text"] = review_text
        if error is not None:
            body["error"] = error
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_SERVICES_URL}/gha-bot/review/{job_id}/result",
                    json=body,
                    headers={"Authorization": f"Bearer {callback_secret}"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "gateway: POST review/%s/result devolveu %d",
                            job_id,
                            resp.status,
                        )
        except Exception:
            logger.exception(
                "gateway: falha ao postar resultado do review_job %s", job_id
            )

    async def _forward_worker(self, queue: asyncio.Queue[_ForwardJob | object]) -> None:
        """Um dos `_MAX_CONCURRENT_FORWARDS` workers fixos da conexão —
        consome a fila até receber `_STOP_WORKER`.

        `_forward` já trata os erros normais de rede/HTTP internamente
        (devolve 502), mas se o WebSocket já estiver fechando, até o
        `ws.send_json` de dentro do `except` de `_forward` pode lançar
        (`ConnectionResetError` do aiohttp). Sem capturar isso AQUI, um
        job problemático mataria o worker de vez — com menos workers
        vivos, o dreno gracioso em `_connect_once` (`queue.put(_STOP_
        WORKER)` um por worker) ficaria esperando um worker que nunca
        mais lê a fila, travando a reconexão.
        """
        while True:
            item = await queue.get()
            try:
                if item is _STOP_WORKER:
                    return
                ws, local_session, req = cast("_ForwardJob", item)
                try:
                    await self._forward(ws, local_session, req)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "gateway: _forward_worker: job falhou de forma"
                        " irrecuperável (ex.: socket já fechando) — job"
                        " descartado, worker segue vivo"
                    )
            finally:
                queue.task_done()

    async def _forward(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        local_session: aiohttp.ClientSession,
        req: GatewayRequestItem,
    ) -> None:
        """Encaminha request ao FastAPI local e devolve response ao gateway."""
        import base64

        req_id: str = req["id"]
        try:
            body_bytes = base64.b64decode(req.get("body", ""))
            async with local_session.request(
                method=req["method"],
                url=f"{self._local_url}{req['path']}",
                headers=req.get("headers", {}),
                data=body_bytes,
            ) as resp:
                resp_body = base64.b64encode(await resp.read()).decode()
                resp_headers = dict(resp.headers)
                async with self._ws_send_lock:
                    await ws.send_json(
                        {
                            "type": "response",
                            "id": req_id,
                            "status": resp.status,
                            "headers": resp_headers,
                            "body": resp_body,
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nunca logar headers/body inteiros — callback OAuth carrega
            # `code`/tokens no corpo, webhooks carregam assinaturas em
            # headers de auth. method/path bastam pro diagnóstico.
            logger.exception(
                "gateway: erro ao encaminhar request %s",
                req_id,
                extra={"method": req.get("method"), "path": req.get("path")},
            )
            async with self._ws_send_lock:
                await ws.send_json(
                    {
                        "type": "response",
                        "id": req_id,
                        "status": 502,
                        "headers": {},
                        "body": "",
                    }
                )


def _machine_fingerprint() -> str:
    """Fingerprint estável por máquina — SHA-256 do machine-id ou hostname."""
    import hashlib
    import socket
    import sys

    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                raw, _ = winreg.QueryValueEx(key, "MachineGuid")
                return hashlib.sha256(str(raw).encode()).hexdigest()[:32]
        except Exception:
            pass
    else:
        for p in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            try:
                if p.is_file():
                    raw = p.read_text().strip()
                    return hashlib.sha256(raw.encode()).hexdigest()[:32]
            except OSError:
                pass

    return hashlib.sha256(socket.getfqdn().encode()).hexdigest()[:32]


__all__ = ["GatewayClient"]
