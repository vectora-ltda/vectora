"""Contratos tipados para pesquisa, extração e navegação web.

Os contratos são independentes do provider. Isso permite trocar Tavily,
DuckDuckGo e o Browser Workbench sem alterar o formato observado pelo agente.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    url: str
    content: str = ""
    provider: str = "browser"


class SearchDiagnostic(BaseModel):
    status: Literal["ok", "empty", "error", "blocked", "timeout", "cancelled"]
    provider: str
    query: str = ""
    url: str | None = None
    elapsed_ms: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    diagnostic: SearchDiagnostic


class FetchedDocument(BaseModel):
    url: str
    final_url: str
    title: str = ""
    content: str = ""
    provider: str = "browser"
    elapsed_ms: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class CrawlRequest(BaseModel):
    url: str
    max_depth: int = Field(default=1, ge=0, le=5)
    limit: int = Field(default=20, ge=1, le=100)
    same_origin: bool = True


class MapRequest(BaseModel):
    url: str
    limit: int = Field(default=50, ge=1, le=200)
    same_origin: bool = True
