"""Headers de autenticação para APIs hospedadas do Vectora Services."""

from __future__ import annotations

from urllib.parse import urlsplit

from backend.services.license import get_license_token

_SERVICES_HOST = "services.vectora.company"


def services_auth_headers(url: str) -> dict[str, str]:
    """Retorna o Vectora token somente para o host oficial do Services."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != _SERVICES_HOST:
        return {}
    token = get_license_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}
