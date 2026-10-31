"""Configurações, armazenamento de credenciais mascaradas e fábrica de provedores SystemOne."""

from __future__ import annotations

import hashlib
import os

from pydantic import BaseModel, Field

from backend.llm.systemone.api_adapters import ClefApiAdapter, JevApiAdapter
from backend.llm.systemone.clef_local import ClefLocalAdapter
from backend.llm.systemone.laya_local import LayaLocalAdapter
from backend.llm.systemone.protocol import SystemOneProvider
from backend.llm.systemone.types import DecisionProviderType


class SystemOneSettings(BaseModel):
    """Configurações centralizadas para o subsistema de Decision Models."""

    enabled: bool = Field(
        default=False, description="Habilita ou desabilita o uso de Decision Models."
    )
    provider: DecisionProviderType = Field(
        default="clef-local",
        description="Provedor ativo: 'clef-local', 'laya-local', 'clef-api' ou 'jev-api'.",
    )
    # Clef local
    local_base_url: str = Field(
        default="http://127.0.0.1:8080",
        description="Endpoint base da instância llama.cpp servindo SystemOne.",
    )
    local_model: str = Field(
        default="clef-flash",
        description="Nome ou caminho do modelo Clef servido no llama.cpp.",
    )

    # Laya local (servidor laya-serve, separado do llama.cpp)
    laya_base_url: str = Field(default="http://127.0.0.1:8000")
    laya_model: str = Field(default="convaiinnovations/laya")
    laya_api_key: str | None = Field(default=None)

    # Clef API
    clef_account_id: str = Field(
        default="",
        description="Account ID da Cloudflare para Workers AI Clef.",
    )
    clef_api_base_url: str = Field(
        default="https://api.cloudflare.com/client/v4/accounts",
        description="Base URL para Clef Cloud.",
    )
    clef_api_model: str = Field(
        default="@cf/cloudflare/clef-flash",
        description="Identificador do modelo na API Clef.",
    )

    # JEV API
    jev_api_base_url: str = Field(
        default="https://api.jev.ai",
        description="Base URL para JEV Cloud.",
    )
    jev_api_model: str = Field(
        default="jev-fast",
        description="Identificador do modelo na API JEV.",
    )

    # Fallback e políticas de execução
    allow_remote_fallback: bool = Field(
        default=False,
        description="Permite fallback para API remota caso local falhe (requer consentimento explícito).",
    )
    confidence_threshold_hitl: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Limiar de confiança abaixo do qual uma decisão requer revisão humana (HITL).",
    )


def mask_secret(secret: str | None) -> str | None:
    """Mascara chaves de API para nunca exibir em texto aberto em logs ou APIs."""
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
    return f"********#{digest}"


def get_systemone_provider(
    settings: SystemOneSettings,
    *,
    clef_api_key: str | None = None,
    jev_api_key: str | None = None,
    local_api_key: str | None = None,
) -> SystemOneProvider:
    """Instancia o provedor de Decision Models ativo de acordo com a configuração."""
    provider_type = settings.provider

    if provider_type == "clef-local":
        return ClefLocalAdapter(
            base_url=settings.local_base_url,
            api_key=local_api_key or os.getenv("LLAMACPP_API_KEY"),
            model=settings.local_model,
        )
    if provider_type == "laya-local":
        return LayaLocalAdapter(
            base_url=settings.laya_base_url,
            model=settings.laya_model,
            api_key=settings.laya_api_key or os.getenv("LAYA_API_KEY"),
        )
    if provider_type == "clef-api":
        key = clef_api_key or os.getenv("CLEF_API_KEY", "")
        account_id = (
            settings.clef_account_id
            or os.getenv("CLOUDFLARE_ACCOUNT_ID")
            or os.getenv("CLEF_ACCOUNT_ID", "")
        )
        if not account_id:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID ou CLEF_ACCOUNT_ID é obrigatório")
        return ClefApiAdapter(
            api_key=key,
            account_id=account_id,
            base_url=settings.clef_api_base_url,
        )
    if provider_type == "jev-api":
        key = jev_api_key or os.getenv("JEV_API_KEY", "")
        return JevApiAdapter(
            api_key=key,
            base_url=settings.jev_api_base_url,
        )
    raise ValueError(f"Provedor de Decision Models desconhecido: {provider_type}")
