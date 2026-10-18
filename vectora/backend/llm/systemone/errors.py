"""Exceções tipadas e hierárquicas para o subsistema SystemOne / Decision Models."""

from __future__ import annotations


class SystemOneError(Exception):
    """Exceção base para todas as falhas de SystemOne / Decision Models."""

    def __init__(
        self, message: str, *, provider: str | None = None, is_retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.is_retryable = is_retryable


class SystemOneConnectionError(SystemOneError):
    """Falha de conectividade ou rede com o provedor (geralmente transitória)."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, is_retryable=True)


class SystemOneTimeoutError(SystemOneError):
    """A requisição excedeu o tempo limite configurado para o modelo de decisão."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, is_retryable=True)


class SystemOneAuthenticationError(SystemOneError):
    """Falha de autenticação (chave inválida ou ausente, 401/403)."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, is_retryable=False)


class SystemOneRateLimitError(SystemOneError):
    """O provedor rejeitou a requisição por exceder limites de taxa (429)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, is_retryable=True)
        self.retry_after = retry_after


class SystemOneValidationError(SystemOneError):
    """Resposta do modelo malformada, incompleta ou fora dos limites acordados (422/Schema)."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, is_retryable=False)


class SystemOneCapabilityError(SystemOneError):
    """A requisição solicitou uma capacidade não suportada pelo runtime/provedor."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, is_retryable=False)
