"""Ponte do jail: loopback do jail → socket Unix do proxy de saída.

Roda dentro do jail (sem rede), antes do comando de verdade: abre um servidor em
`127.0.0.1:PORTA` que repassa cada conexão ao socket Unix montado pelo host, e
em seguida troca de processo (`exec`) pelo comando. O jail nunca recebe uma
rota de rede; só consegue pedir ao proxy, que decide.

    python -m backend.sandbox.egress_bridge --socket S --port P -- comando...
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import os
import signal
import sys
from collections.abc import Callable

_READY_TIMEOUT_S = 5.0
_PR_SET_PDEATHSIG = 1


async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def serve(
    socket_path: str, port: int, on_ready: Callable[[], None] | None = None
) -> None:
    """Escuta em `127.0.0.1:port` e liga cada conexão ao socket do proxy."""

    async def handle(
        client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_unix_connection(
                socket_path
            )
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(
            _forward(client_reader, upstream_writer),
            _forward(upstream_reader, client_writer),
        )

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    if on_ready is not None:
        on_ready()
    async with server:
        await server.serve_forever()


def _die_with_parent() -> None:
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL(None).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


def run_then_exec(socket_path: str, port: int, command: list[str]) -> None:
    """Sobe a ponte num processo filho e troca este processo pelo `command`."""
    ready_read, ready_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_read)
        _die_with_parent()

        def announce() -> None:
            os.write(ready_write, b"1")
            os.close(ready_write)

        try:
            asyncio.run(serve(socket_path, port, announce))
        finally:
            os._exit(0)
    os.close(ready_write)
    # Só executa o comando depois de a porta estar de pé, para a primeira
    # conexão do comando não correr contra a ponte.
    import select

    ready, _, _ = select.select([ready_read], [], [], _READY_TIMEOUT_S)
    os.close(ready_read)
    if not ready:
        sys.stderr.write("vectora: a ponte de saída do jail não iniciou\n")
        os.kill(pid, signal.SIGKILL)
        os._exit(70)
    # O comando é o do próprio jail (o worker ou o shell do usuário), nunca
    # uma entrada externa: troca este processo por ele.
    os.execvp(command[0], command)  # noqa: S606  # nosec B606


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("falta o comando a executar")
    run_then_exec(args.socket, args.port, command)


if __name__ == "__main__":
    main()
