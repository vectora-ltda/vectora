"""Preparo do jail por dentro: ponte de rede e endurecimento, antes do comando.

Roda dentro do jail, antes do comando de verdade, e em seguida troca de
processo (`exec`) por ele:

- com `--socket` e `--port`, abre um servidor em `127.0.0.1:PORTA` que repassa
  cada conexão ao socket Unix do proxy de saída montado pelo host (o jail nunca
  recebe uma rota de rede; só consegue pedir ao proxy, que decide);
- com `--harden`, aplica limites de recursos, `no_new_privs` e Landlock (com as
  pastas que o `bwrap` declarou nas variáveis `VECTORA_SANDBOX_*`), que o
  comando herda. É o que o worker faz sozinho; os demais processos do jail
  (terminal interativo, servidores MCP) passam por aqui.

    python -m backend.sandbox.egress_bridge [--socket S --port P] [--harden] -- comando...
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


def harden_process() -> None:
    """Limites de recursos, `no_new_privs` e Landlock deste processo, com as
    pastas e portas que o `bwrap` passou nas variáveis `VECTORA_SANDBOX_*`."""
    import json

    from backend.sandbox.landlock import apply_landlock, set_no_new_privs
    from backend.sandbox.rlimits import apply_rlimits, limits_from_environment

    max_file_mb, memory_mb = limits_from_environment()
    apply_rlimits(
        lockdown=os.environ.get("VECTORA_SANDBOX_LOCKDOWN") == "1",
        max_file_mb=max_file_mb,
        memory_limit_mb=memory_mb,
    )
    set_no_new_privs()
    workspace_dir = os.environ.get("VECTORA_SANDBOX_WORKSPACE_DIR")
    if not workspace_dir:
        return

    def paths(name: str) -> list[str]:
        return [str(item) for item in json.loads(os.environ.get(name, "[]"))]

    apply_landlock(
        [
            workspace_dir,
            *paths("VECTORA_SANDBOX_RW_PATHS"),
            *paths("VECTORA_SANDBOX_BASE_RW_PATHS"),
        ],
        [*paths("VECTORA_SANDBOX_RO_PATHS"), *paths("VECTORA_SANDBOX_BASE_RO_PATHS")],
        tuple(int(port) for port in paths("VECTORA_SANDBOX_ALLOW_TCP_PORTS")),
    )


def run_then_exec(
    socket_path: str | None,
    port: int | None,
    command: list[str],
    *,
    harden: bool = False,
) -> None:
    """Sobe a ponte (se pedida) num processo filho, endurece este processo (se
    pedido) e o troca pelo `command`."""
    if socket_path is None or port is None:
        if harden:
            harden_process()
        os.execvp(command[0], command)  # noqa: S606  # nosec B606
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
    if harden:
        harden_process()
    # O comando é o do próprio jail (o worker ou o shell do usuário), nunca
    # uma entrada externa: troca este processo por ele.
    os.execvp(command[0], command)  # noqa: S606  # nosec B606


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket")
    parser.add_argument("--port", type=int)
    parser.add_argument("--harden", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("falta o comando a executar")
    if (args.socket is None) != (args.port is None):
        parser.error("--socket e --port andam juntos")
    run_then_exec(args.socket, args.port, command, harden=args.harden)


if __name__ == "__main__":
    main()
