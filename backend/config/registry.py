"""Schema declarativo de configuração — fonte única de verdade compartilhada
entre CLI, REST e (indiretamente) frontend, para evitar a duplicação que já
causou bug real: `PATCH /admin/api-keys` e `POST /auth/envs` tinham cada um
sua própria cópia da lógica "grava no .env + atualiza os.environ + atualiza
settings" (ver `backend/services/env_keys.py`, criado justamente pra
consolidar isso).

Este módulo não substitui nenhum dos 4 mecanismos de persistência existentes
(`.env` via `env_keys.py`, `RuntimeSettings` em SQLite, tabelas SQLite ad hoc
do `provider_routing.py`, `config.toml` via `license.py`) — só declara, por
cima deles, um contrato tipado único: uma chave, uma categoria, uma
descrição, um adapter que sabe ler/escrever no mecanismo real.

Escopo deliberado: só campos que são genuinamente um par chave→valor
escalar entram aqui. Recursos em formato de coleção (modelos registrados por
gateway em `provider_routing.py`, memórias em `memory.py`, perfil de conta em
`/auth/me`) não são forçados nesse molde — continuam com seus próprios
comandos/endpoints especializados (CRUD de verdade, não get/set de uma
chave). Ver `backend/cli/config.py` para onde as duas formas coexistem.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable


class SettingAdapter(Protocol):
    """Lê/escreve um valor escalar num mecanismo de persistência real."""

    def get(self, key: str) -> object: ...
    def set(self, key: str, value: object) -> None: ...


@dataclasses.dataclass(frozen=True)
class SettingField:
    """Um campo de configuração declarado — metadados + o adapter que
    resolve a leitura/escrita real."""

    key: str
    category: str
    cli_flag: str
    description: str
    adapter: SettingAdapter
    secret: bool = False

    def get(self) -> object:
        return self.adapter.get(self.key)

    def set(self, value: object) -> None:
        self.adapter.set(self.key, value)


_REGISTRY: dict[str, SettingField] = {}


class DuplicateSettingFieldError(ValueError):
    """Duas definições tentaram registrar a mesma chave."""


def setting_field(
    key: str,
    *,
    category: str,
    cli_flag: str,
    description: str,
    adapter: SettingAdapter,
    secret: bool = False,
) -> SettingField:
    """Registra um campo de configuração no schema declarativo.

    Chamado nos módulos de definição (``backend/config/fields.py``) — a
    importação desses módulos é o que popula o registry; nada acontece só
    por importar ``registry.py`` sozinho.
    """
    if key in _REGISTRY:
        raise DuplicateSettingFieldError(f"setting_field duplicado: {key!r}")
    field = SettingField(
        key=key,
        category=category,
        cli_flag=cli_flag,
        description=description,
        adapter=adapter,
        secret=secret,
    )
    _REGISTRY[key] = field
    return field


def get_field(key: str) -> SettingField | None:
    return _REGISTRY.get(key)


def get_value(key: str) -> object:
    """Read a registered value through its declared adapter."""
    field = get_field(key)
    if field is None:
        raise KeyError(f"setting não registrado: {key}")
    return field.get()


def set_value(key: str, value: object) -> None:
    """Write a registered value through its declared adapter."""
    field = get_field(key)
    if field is None:
        raise KeyError(f"setting não registrado: {key}")
    field.set(value)


def fields_for_category(category: str) -> list[SettingField]:
    return [f for f in _REGISTRY.values() if f.category == category]


def all_categories() -> list[str]:
    return sorted({f.category for f in _REGISTRY.values()})


def all_fields() -> Iterable[SettingField]:
    return _REGISTRY.values()


def _reset_registry_for_tests() -> None:
    """Só para testes: limpa o registry global entre casos isolados."""
    _REGISTRY.clear()
