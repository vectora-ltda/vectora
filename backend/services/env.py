"""Environment Variable Management with Strict Validation.

Provides typed access to environment variables with optional strict mode.
Includes custom exceptions for missing required configuration.
"""

import os
from typing import Literal, overload


class GetEnvError(Exception):
    """Configuração ausente (env var obrigatória não setada).

    É um ``Exception`` (não ``BaseException``) para ser capturável pelos
    handlers defensivos do pipeline de stream e classificável como
    ``MISSING_KEYS`` — em vez de escapar cru e virar um crash genérico.
    """


@overload
def get_env(name: str) -> str: ...


@overload
def get_env(name: str, *, strict: Literal[True]) -> str: ...


@overload
def get_env(name: str, *, strict: Literal[False]) -> str | None: ...


def get_env(name: str, *, strict: bool = True) -> str | None:
    """Get environment variable with optional strict validation."""
    value = os.getenv(name)

    if value is None and strict:
        msg = f"Env variable {name!r} does not exist"
        raise GetEnvError(msg)

    return value
