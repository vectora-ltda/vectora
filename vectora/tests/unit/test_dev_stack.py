"""Testes do dev_stack — defaults de infra local e comandos docker.

Garante que:
- as URLs default (defaults.env) batem com as constantes do dev_stack;
- os specs dos serviços usam as mesmas imagens do docker-compose.yml;
- o probe redis_reachable faz get_kv/get_mq caírem para memória quando o
  redis_url default está setado mas o serviço não está de pé.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.storage.dev_stack import (
    DEFAULT_POSTGRES_DSN,
    DEFAULT_QDRANT_API_KEY,
    DEFAULT_QDRANT_URL,
    DEFAULT_REDIS_URL,
    SERVICES,
    connection_urls,
    docker_run_cmd,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Consistência defaults.env ↔ dev_stack ↔ compose.dev.yml
# ---------------------------------------------------------------------------


def test_defaults_env_contem_urls_da_stack() -> None:
    defaults = (REPO_ROOT / "backend" / "defaults.env").read_text(encoding="utf-8")
    assert f"POSTGRES_DSN={DEFAULT_POSTGRES_DSN}" in defaults
    assert f"REDIS_URL={DEFAULT_REDIS_URL}" in defaults
    assert f"QDRANT_URL={DEFAULT_QDRANT_URL}" in defaults
    assert f"QDRANT_API_KEY={DEFAULT_QDRANT_API_KEY}" in defaults


def test_nenhum_servico_sem_credencial() -> None:
    """Auth em todos os serviços, mesmo em dev — Redis com senha na URL
    (mesmo formato do Postgres) e Qdrant com API key."""
    assert "redis://:" in DEFAULT_REDIS_URL  # senha embutida (redis://:senha@host)
    assert DEFAULT_QDRANT_API_KEY == "vectora"
    redis = next(s for s in SERVICES if s.name == "vectora-redis")
    assert "--requirepass" in redis.command
    qdrant = next(s for s in SERVICES if s.name == "vectora-qdrant")
    assert any(e.startswith("QDRANT__SERVICE__API_KEY=") for e in qdrant.env)


def test_portas_publicadas_apenas_em_localhost() -> None:
    for spec in SERVICES:
        for port in spec.ports:
            assert port.startswith("127.0.0.1:"), (
                f"{spec.name}: porta {port} exposta além de localhost"
            )


def test_settings_carregam_defaults_de_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sem env explícito, os defaults embarcados devem preencher os campos.
    for key in ("POSTGRES_DSN", "REDIS_URL", "QDRANT_URL", "QDRANT_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    from backend.settings import Settings

    s = Settings()
    assert s.postgres_dsn == DEFAULT_POSTGRES_DSN
    assert s.redis_url == DEFAULT_REDIS_URL
    assert s.qdrant_url == DEFAULT_QDRANT_URL
    assert s.qdrant_api_key == DEFAULT_QDRANT_API_KEY


def test_specs_usam_imagens_compativeis_com_compose() -> None:
    """Postgres e Qdrant usam a mesma imagem no dev_stack e no docker-compose.yml.

    O Redis diverge de propósito: o dev_stack usa a imagem leve; o compose
    usa redis-stack-server, que traz os módulos RediSearch/RedisJSON.
    """
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("vectora-postgres", "vectora-qdrant"):
        spec = next(s for s in SERVICES if s.name == name)
        assert spec.image in compose, f"imagem {spec.image} divergiu do compose"
    assert "redis/redis-stack-server" in compose


def test_connection_urls_mapeia_constantes() -> None:
    urls = connection_urls()
    assert urls == {
        "POSTGRES_DSN": DEFAULT_POSTGRES_DSN,
        "REDIS_URL": DEFAULT_REDIS_URL,
        "QDRANT_URL": DEFAULT_QDRANT_URL,
        "QDRANT_API_KEY": DEFAULT_QDRANT_API_KEY,
    }


# ---------------------------------------------------------------------------
# connection_defaults — pré-preenchimento do Setup Wizard
# ---------------------------------------------------------------------------


def test_connection_defaults_traz_url_e_start_command() -> None:
    """Cada serviço expõe a URL default e o comando self-hosted que o sobe."""
    from backend.storage.dev_stack import connection_defaults

    defaults = connection_defaults()
    assert defaults["postgres"]["url"] == DEFAULT_POSTGRES_DSN
    assert defaults["redis"]["url"] == DEFAULT_REDIS_URL
    assert defaults["qdrant"]["url"] == DEFAULT_QDRANT_URL
    # Qdrant também traz a API key (auth-first).
    assert defaults["qdrant"]["api_key"] == DEFAULT_QDRANT_API_KEY
    # Todos trazem o comando self-hosted (docker compose up -d <serviço>).
    for service in ("postgres", "redis", "qdrant"):
        cmd = defaults[service]["start_command"]
        assert "docker compose up -d" in cmd
        assert service in cmd


def test_connection_defaults_url_redis_tem_senha() -> None:
    """Borda: a URL default do Redis embute a senha que o compose configura.

    Sem senha na URL, o auto-test do wizard falha no AUTH contra o Redis que
    o compose sobe com --requirepass.
    """
    from backend.storage.dev_stack import connection_defaults

    redis_url = connection_defaults()["redis"]["url"]
    assert redis_url.startswith("redis://:")  # redis://:<senha>@host
    assert "@" in redis_url


# ---------------------------------------------------------------------------
# docker-compose.yml ↔ defaults — credenciais consistentes
# ---------------------------------------------------------------------------


def _redis_password_from_url(url: str) -> str:
    # redis://:<senha>@host:porta/db
    return url.split("://:", 1)[1].split("@", 1)[0]


def test_compose_redis_exige_senha_dos_defaults() -> None:
    """O Redis do compose sobe com a MESMA senha embutida em DEFAULT_REDIS_URL.

    Garante que `docker compose up` + defaults do Vectora conectam sem ajuste:
    o auto-test do wizard fica verde sozinho.
    """
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    password = _redis_password_from_url(DEFAULT_REDIS_URL)
    assert "--requirepass" in compose, "compose redis precisa de --requirepass"
    assert password in compose, (
        f"senha {password!r} do DEFAULT_REDIS_URL ausente no compose"
    )


def test_compose_qdrant_define_api_key_dos_defaults() -> None:
    """O Qdrant do compose sobe com a MESMA API key de DEFAULT_QDRANT_API_KEY."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "QDRANT__SERVICE__API_KEY" in compose
    assert DEFAULT_QDRANT_API_KEY in compose


# ---------------------------------------------------------------------------
# docker run — estrutura dos comandos
# ---------------------------------------------------------------------------


def test_docker_run_cmd_postgres() -> None:
    spec = next(s for s in SERVICES if s.name == "vectora-postgres")
    cmd = docker_run_cmd(spec)
    assert cmd[:3] == ["docker", "run", "-d"]
    assert "--name" in cmd and "vectora-postgres" in cmd
    assert "-p" in cmd and "127.0.0.1:5432:5432" in cmd
    assert "POSTGRES_USER=vectora" in cmd
    assert cmd[-1] == "pgvector/pgvector:pg16"  # sem command extra


def test_docker_run_cmd_redis_inclui_command() -> None:
    spec = next(s for s in SERVICES if s.name == "vectora-redis")
    cmd = docker_run_cmd(spec)
    image_idx = cmd.index("redis:7-alpine")
    assert cmd[image_idx + 1 :] == [
        "redis-server",
        "--appendonly",
        "yes",
        "--maxmemory",
        "256mb",
        "--maxmemory-policy",
        "allkeys-lru",
        "--requirepass",
        "vectora",
    ]


def test_parser_aceita_storage_up_down() -> None:
    from backend.main import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["storage", "up"]).action == "up"
    assert parser.parse_args(["storage", "down"]).action == "down"


# ---------------------------------------------------------------------------
# Drift de container — recriar quando o command divergiu do spec
# ---------------------------------------------------------------------------


def test_container_matches_spec_quando_cmd_igual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container cujo Cmd bate com o spec não precisa de recriação."""
    import json

    from backend.storage import dev_stack

    redis = next(s for s in SERVICES if s.name == "vectora-redis")

    def _fake_run(cmd: list[str]):
        class _P:
            returncode = 0
            stdout = json.dumps(list(redis.command))

        return _P()

    monkeypatch.setattr(dev_stack, "_run", _fake_run)
    assert dev_stack._container_matches_spec("vectora-redis", redis) is True


def test_container_nao_bate_quando_falta_requirepass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erro/borda: container legado sem --requirepass diverge e deve ser recriado.

    Reproduz o AUTH error: um vectora-redis criado antes da senha no spec roda
    sem --requirepass; a URL default (redis://:vectora@...) então falha no AUTH.
    """
    import json

    from backend.storage import dev_stack

    redis = next(s for s in SERVICES if s.name == "vectora-redis")
    legacy_cmd = ["redis-server", "--appendonly", "yes"]  # sem --requirepass

    def _fake_run(cmd: list[str]):
        class _P:
            returncode = 0
            stdout = json.dumps(legacy_cmd)

        return _P()

    monkeypatch.setattr(dev_stack, "_run", _fake_run)
    assert dev_stack._container_matches_spec("vectora-redis", redis) is False


def test_container_matches_spec_sem_command_sempre_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Specs sem command (Postgres/Qdrant) não checam Cmd — nada a comparar."""
    from backend.storage import dev_stack

    postgres = next(s for s in SERVICES if s.name == "vectora-postgres")

    def _fail(cmd: list[str]):  # não deve ser chamado
        raise AssertionError("não deveria inspecionar container sem command")

    monkeypatch.setattr(dev_stack, "_run", _fail)
    assert dev_stack._container_matches_spec("vectora-postgres", postgres) is True


def test_container_matches_spec_inspect_falha_recria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Borda: se docker inspect falha, trata como divergente (recria por segurança)."""
    from backend.storage import dev_stack

    redis = next(s for s in SERVICES if s.name == "vectora-redis")

    def _fake_run(cmd: list[str]):
        class _P:
            returncode = 1
            stdout = ""

        return _P()

    monkeypatch.setattr(dev_stack, "_run", _fake_run)
    assert dev_stack._container_matches_spec("vectora-redis", redis) is False


# ---------------------------------------------------------------------------
# Probe de conectividade — fallback para memória com redis fora do ar
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_singletons(_no_nats_sidecar):
    from backend.persistence.kv import reset_kv, reset_reachable_cache
    from backend.scheduling.mq import reset_mq

    reset_kv()
    reset_mq()
    reset_reachable_cache()
    yield
    reset_kv()
    reset_mq()
    reset_reachable_cache()


@pytest.mark.asyncio
async def test_get_kv_cai_para_memoria_com_redis_inacessivel(
    monkeypatch: pytest.MonkeyPatch, _reset_singletons
) -> None:
    from backend.persistence.kv import MemoryKV, get_kv
    from backend.settings import settings

    # Porta 9 (discard) — nada escutando; probe deve falhar rápido.
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:9/0")
    assert isinstance(await get_kv(), MemoryKV)


@pytest.mark.asyncio
async def test_get_mq_cai_para_memoria_com_redis_inacessivel(
    monkeypatch: pytest.MonkeyPatch, _reset_singletons
) -> None:
    from backend.scheduling.mq import MemoryMQ, get_mq
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:9/0")
    assert isinstance(await get_mq(), MemoryMQ)


def test_redis_reachable_cacheia_resultado(_reset_singletons) -> None:
    from backend.persistence import kv

    assert kv.redis_reachable("redis://127.0.0.1:9/0", timeout=0.1) is False
    assert "redis://127.0.0.1:9/0" in kv._reachable_cache
