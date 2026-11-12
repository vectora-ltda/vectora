from __future__ import annotations

from backend.services import service_auth


def test_sends_account_token_only_to_official_services_host(monkeypatch) -> None:
    monkeypatch.setattr(
        service_auth, "get_license_token", lambda: "vectora-account-token"
    )

    assert service_auth.services_auth_headers(
        "https://services.vectora.company/library/mcp"
    ) == {"Authorization": "Bearer vectora-account-token"}
    assert service_auth.services_auth_headers("https://catalog.example/registry") == {}
    assert (
        service_auth.services_auth_headers(
            "http://services.vectora.company/registry/mcp"
        )
        == {}
    )


def test_does_not_create_authorization_header_without_account_token(
    monkeypatch,
) -> None:
    monkeypatch.setattr(service_auth, "get_license_token", lambda: None)

    assert (
        service_auth.services_auth_headers(
            "https://services.vectora.company/library/mcp"
        )
        == {}
    )
