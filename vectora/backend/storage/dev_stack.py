"""Stack local de infra — Postgres+pgvector, Redis e Qdrant via Docker.

Fonte única dos defaults de conexão e dos comandos `docker run` usados por
``vectora storage up``/``down``. As imagens batem com o ``docker-compose.yml``
de produção (modo completo); portas/credenciais divergem de propósito — aqui
publicamos em ``127.0.0.1`` com senha, enquanto o compose roda em rede interna.

Estratégia do ``up``:
  1. Se o repo tem um ``compose.dev.yml`` ao alcance (hook opcional de dev),
     usa ``docker compose -f ... up -d``.
  2. Senão (caso padrão / instalação via wheel/pipx), sobe os 3 containers com
     ``docker run`` equivalentes, nomeados ``vectora-*`` e com volumes
     nomeados — reaproveitados em execuções futuras via ``docker start``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess  # nosec B404 — apenas comandos docker montados internamente
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Defaults de conexão (mesmos valores do backend/defaults.env) ────────────

DEFAULT_POSTGRES_DSN = "postgresql+asyncpg://vectora:vectora@localhost:5432/vectora"
DEFAULT_REDIS_URL = "redis://:vectora@localhost:6379/0"  # nosec B105 — dev local
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_QDRANT_API_KEY = "vectora"  # nosec B105 — dev local


@dataclass(frozen=True)
class ServiceSpec:
    """Um serviço da infra local — espelho 1:1 do compose.dev.yml."""

    name: str  # nome do container (vectora-*)
    image: str  # mesma imagem do docker-compose.yml de produção
    ports: tuple[str, ...]
    volumes: tuple[str, ...]
    env: tuple[str, ...] = ()
    command: tuple[str, ...] = ()


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        name="vectora-postgres",
        image="pgvector/pgvector:pg16",
        ports=("127.0.0.1:5432:5432",),
        volumes=("vectora-postgres:/var/lib/postgresql/data",),
        env=(
            "POSTGRES_USER=vectora",
            "POSTGRES_PASSWORD=vectora",  # nosec B105 — dev local, igual ao compose.dev.yml
            "POSTGRES_DB=vectora",
        ),
    ),
    ServiceSpec(
        name="vectora-redis",
        image="redis:7-alpine",
        ports=("127.0.0.1:6379:6379",),
        volumes=("vectora-redis:/data",),
        command=(
            "redis-server",
            "--appendonly",
            "yes",
            "--maxmemory",
            "256mb",
            "--maxmemory-policy",
            "allkeys-lru",
            "--requirepass",
            "vectora",
        ),
    ),
    ServiceSpec(
        name="vectora-qdrant",
        image="qdrant/qdrant:latest",
        ports=("127.0.0.1:6333:6333", "127.0.0.1:6334:6334"),
        volumes=("vectora-qdrant:/qdrant/storage",),
        env=(
            "QDRANT__SERVICE__GRPC_PORT=6334",
            "QDRANT__SERVICE__API_KEY=vectora",
        ),
    ),
)


def docker_run_cmd(spec: ServiceSpec) -> list[str]:
    """Monta o ``docker run`` equivalente para subir o serviço em dev local."""
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        spec.name,
        "--restart",
        "unless-stopped",
    ]
    for port in spec.ports:
        cmd += ["-p", port]
    for env in spec.env:
        cmd += ["-e", env]
    for volume in spec.volumes:
        cmd += ["-v", volume]
    cmd.append(spec.image)
    cmd += list(spec.command)
    return cmd


def compose_file() -> Path | None:
    """Localiza o ``docker-compose.yml`` do repo (infra: postgres, redis, qdrant)."""
    candidate = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
    return candidate if candidate.is_file() else None


# ── Execução ──────────────────────────────────────────────────────────────


@dataclass
class StackResult:
    ok: bool
    messages: list[str] = field(default_factory=list)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — comandos docker montados internamente  # nosec B603
        cmd, capture_output=True, text=True, timeout=60, check=False
    )


def _run_detached(cmd: list[str]) -> int:
    """Roda comando docker que inicia processos em background (up -d, run -d, start).

    Descarta stdout/stderr para evitar o deadlock de herança de pipe no Windows:
    quando um processo filho herda os handles de pipe e o pai sai, _readerthread.join()
    bloqueia indefinidamente aguardando o pipe fechar.
    """
    proc = subprocess.run(  # noqa: S603  # nosec B603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return proc.returncode


def _docker_available() -> bool:
    try:
        return (
            _run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _existing_containers() -> set[str]:
    proc = _run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _container_matches_spec(name: str, spec: ServiceSpec) -> bool:
    """True se o container existente foi criado com o ``command`` do spec atual.

    Compara o ``Config.Cmd`` do container com ``spec.command``. Quando o spec
    muda (ex.: adição de ``--requirepass``), um container antigo roda com a
    config anterior — reiniciá-lo via ``docker start`` propaga a divergência
    (no caso do Redis, AUTH falha porque o servidor subiu sem senha). Specs sem
    ``command`` (Postgres/Qdrant configuram via env) não têm o que comparar e
    sempre batem. Falha do ``docker inspect`` é tratada como divergência para
    forçar recriação por segurança.
    """
    if not spec.command:
        return True
    proc = _run(["docker", "inspect", "--format", "{{json .Config.Cmd}}", name])
    if proc.returncode != 0:
        return False
    try:
        actual = json.loads(proc.stdout.strip()) or []
    except (json.JSONDecodeError, ValueError):
        return False
    return list(actual) == list(spec.command)


def stack_up() -> StackResult:
    """Sobe Postgres, Redis e Qdrant — compose quando disponível, docker run senão."""
    result = StackResult(ok=True)

    if not _docker_available():
        result.ok = False
        result.messages.append(
            "Docker não encontrado ou daemon parado. Instale/inicie o Docker "
            "Desktop (ou docker-ce) e tente de novo."
        )
        return result

    compose = compose_file()
    if compose is not None:
        rc = _run_detached(["docker", "compose", "-f", str(compose), "up", "-d"])
        if rc == 0:
            result.messages.append(f"docker compose up -d ({compose})")
        else:
            if os.getenv("CI") and all(
                _tcp_ready(host, port)
                for host, port in (
                    ("127.0.0.1", 5432),
                    ("127.0.0.1", 6379),
                    ("127.0.0.1", 6333),
                )
            ):
                result.messages.append("CI services already running")
                return result
            result.ok = False
            result.messages.append("docker compose up -d falhou")
        return result

    existing = _existing_containers()
    for spec in SERVICES:
        # Container existente cujo command divergiu do spec (ex.: legado sem
        # --requirepass) é removido para ser recriado com a config atual. O
        # volume nomeado persiste, então nenhum dado é perdido.
        if spec.name in existing and not _container_matches_spec(spec.name, spec):
            _run_detached(["docker", "rm", "-f", spec.name])
            existing.discard(spec.name)
            result.messages.append(f"{spec.name}: config divergente — recriando")

        if spec.name in existing:
            rc = _run_detached(["docker", "start", spec.name])
            action = "start"
        else:
            rc = _run_detached(docker_run_cmd(spec))
            action = "run"
        if rc == 0:
            result.messages.append(f"{spec.name}: docker {action} ok")
        else:
            result.ok = False
            result.messages.append(f"{spec.name}: falha no docker {action}")
    return result


def _tcp_ready(host: str, port: int) -> bool:
    """Return whether a local TCP service is already accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def stack_down() -> StackResult:
    """Para os containers da infra local (sem apagar volumes)."""
    result = StackResult(ok=True)

    if not _docker_available():
        result.ok = False
        result.messages.append("Docker não encontrado ou daemon parado.")
        return result

    compose = compose_file()
    if compose is not None:
        rc = _run_detached(["docker", "compose", "-f", str(compose), "down"])
        if rc == 0:
            result.messages.append(f"docker compose down ({compose})")
        else:
            result.ok = False
            result.messages.append("docker compose down falhou")
        return result

    existing = _existing_containers()
    for spec in SERVICES:
        if spec.name not in existing:
            result.messages.append(f"{spec.name}: não existe — nada a fazer")
            continue
        rc = _run_detached(["docker", "stop", spec.name])
        if rc == 0:
            result.messages.append(f"{spec.name}: parado")
        else:
            result.ok = False
            result.messages.append(f"{spec.name}: falha ao parar")
    return result


def _running_containers() -> set[str]:
    proc = _run(["docker", "ps", "--format", "{{.Names}}"])
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def stack_status() -> StackResult:
    """Estado de cada serviço da infra local: rodando, parado ou ausente."""
    result = StackResult(ok=True)

    if not _docker_available():
        result.ok = False
        result.messages.append("Docker não encontrado ou daemon parado.")
        return result

    existing = _existing_containers()
    running = _running_containers()
    for spec in SERVICES:
        if spec.name in running:
            result.messages.append(f"{spec.name}: rodando")
        elif spec.name in existing:
            result.messages.append(f"{spec.name}: parado")
        else:
            result.messages.append(f"{spec.name}: ausente")
    return result


def connection_urls() -> dict[str, str]:
    """URLs de conexão da stack local — as mesmas do defaults.env."""
    return {
        "POSTGRES_DSN": DEFAULT_POSTGRES_DSN,
        "REDIS_URL": DEFAULT_REDIS_URL,
        "QDRANT_URL": DEFAULT_QDRANT_URL,
        "QDRANT_API_KEY": DEFAULT_QDRANT_API_KEY,
    }


#: Comando self-hosted que sobe cada serviço — bate com o ``docker-compose.yml``
#: da raiz (mesmo arquivo usado por ``scons docker``). O Setup Wizard preenche o
#: campo "Comando de start" com isto.
DEFAULT_START_COMMANDS: dict[str, str] = {
    "postgres": "docker compose up -d postgres",
    "redis": "docker compose up -d redis",
    "qdrant": "docker compose up -d qdrant",
}


def connection_defaults() -> dict[str, dict[str, str]]:
    """Config default por serviço para pré-preencher o Setup Wizard.

    Fonte única dos valores que ``docker compose up`` cria — URL, API key
    (Qdrant) e o comando self-hosted que sobe o serviço. O wizard preenche os
    campos com isto para conexão automática sem digitação manual.
    """
    return {
        "postgres": {
            "url": DEFAULT_POSTGRES_DSN,
            "start_command": DEFAULT_START_COMMANDS["postgres"],
        },
        "redis": {
            "url": DEFAULT_REDIS_URL,
            "start_command": DEFAULT_START_COMMANDS["redis"],
        },
        "qdrant": {
            "url": DEFAULT_QDRANT_URL,
            "api_key": DEFAULT_QDRANT_API_KEY,
            "start_command": DEFAULT_START_COMMANDS["qdrant"],
        },
    }
