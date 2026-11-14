"""Lançador, no host, dos jails que o backend não controla por um worker.

O filtro seccomp precisa chegar ao `bwrap` por um descritor herdado, e quem
inicia um servidor MCP (`stdio_client`) ou um terminal interativo (PTY) só
informa um comando. Este módulo é esse comando: compila o filtro, o deixa num
descritor herdável e troca o processo pelo `bwrap`.

    python -m backend.sandbox.jail_exec [--require-seccomp] -- bwrap ...
"""

from __future__ import annotations

import argparse
import os
import sys

from backend.sandbox.linux import build_seccomp_filter


def with_seccomp(argv: list[str], *, require: bool = False) -> list[str]:
    """`argv` do `bwrap` com `--seccomp FD`, deixando o descritor herdável.

    Sem libseccomp o jail segue sem o filtro (isolamento parcial, avisado em
    `stderr`), salvo com `require`, que recusa iniciar."""
    bpf = build_seccomp_filter()
    if bpf is None:
        if require:
            sys.stderr.write(
                "vectora: isolamento completo exigido, mas o seccomp está indisponível\n"
            )
            raise SystemExit(70)
        sys.stderr.write("vectora: seccomp indisponível; jail com isolamento parcial\n")
        return argv
    fd = os.memfd_create("vectora-seccomp", 0)  # sem CLOEXEC: o bwrap herda
    os.write(fd, bpf)
    os.lseek(fd, 0, os.SEEK_SET)
    os.set_inheritable(fd, True)
    return [argv[0], "--seccomp", str(fd), *argv[1:]]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-seccomp", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("falta o comando do bwrap")
    final = with_seccomp(command, require=args.require_seccomp)
    # O comando é o `bwrap` montado pelo próprio backend, nunca uma entrada externa.
    os.execvp(final[0], final)  # noqa: S606  # nosec B606


if __name__ == "__main__":
    main()
