"""Módulo de secrets do Vectora.

Exporta o provider ativo conforme configuração em ~/.vectora/secrets.toml.
Default: KeePassXC .kdbx (pykeepass).
Fallback: SQLite + PyNaCl (internal).

Uso típico:
    from vectora.services.secrets import get_secrets_provider
    provider = get_secrets_provider()
    await provider.unlock(user_id, login_password)
    value = await provider.get(user_id, "GH_TOKEN")
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PROVIDER_ENV = os.getenv("VECTORA_SECRETS_PROVIDER", "keepass").lower()


def get_secrets_provider() -> Any:
    """Retorna o provider de secrets configurado.

    Ordem de resolução:
    1. Env VECTORA_SECRETS_PROVIDER (keepass | internal)
    2. ~/.vectora/secrets.toml → provider = "..."
    3. Default: keepass
    """
    provider_name = _PROVIDER_ENV

    # Tenta ler do secrets.toml se existir
    from pathlib import Path

    toml_path = Path.home() / ".vectora" / "secrets.toml"
    if toml_path.exists() and provider_name == "keepass":
        try:
            import tomllib

            config = tomllib.loads(toml_path.read_text())
            provider_name = config.get("provider", "keepass").lower()
        except Exception as exc:
            logger.debug("secrets: falha ao ler secrets.toml: %s", exc)

    if provider_name == "internal":
        from backend.secrets.internal import InternalSecretsProvider

        return InternalSecretsProvider()

    # Default: keepass
    try:
        from backend.secrets.keepass import get_provider

        return get_provider()
    except ImportError:
        logger.warning("secrets: pykeepass não disponível, usando fallback interno")
        from backend.secrets.internal import InternalSecretsProvider

        return InternalSecretsProvider()


__all__ = ["get_secrets_provider"]
