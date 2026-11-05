"""Recipe Qdrant Cloud — vetor gerenciado com suporte HYBRID (dense + BM25 sparse).

Qdrant Cloud oferece clusters gerenciados em AWS/GCP/Azure. Para o Vectora:

    * URL do cluster: ``https://<cluster-id>.<region>.aws.cloud.qdrant.io``
    * API key obrigatória (QDRANT_API_KEY).
    * Modo HYBRID = ``RetrievalMode.HYBRID``: dense (Cohere) + sparse (BM25
      via ``FastEmbedSparse``). Maximiza recall sem custo extra.
    * Collections: ``articles``, ``web_cache``, ``search`` (mesmas do modo lite).

Uso:
    >>> from backend.storage.recipes.qdrant_cloud import build_config, healthcheck
    >>> cfg = build_config(
    ...     url="https://my-cluster.us-east-1.aws.cloud.qdrant.io",
    ...     api_key="minha-api-key",
    ... )
    >>> result = await healthcheck(cfg["url"], cfg["api_key"])
    >>> result["ok"]
    True
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# URL template para clusters Qdrant Cloud
URL_TEMPLATE = "https://{cluster_id}.{region}.aws.cloud.qdrant.io"


def build_config(
    *,
    url: str = "",
    cluster_id: str = "",
    region: str = "us-east-1",
    provider: str = "aws",
    api_key: str,
    collections: list[str] | None = None,
    retrieval_mode: str = "hybrid",
) -> dict[str, Any]:
    """Monta a configuração para QdrantVectorStore.

    Args:
        url:            URL direta do cluster (override dos campos ``cluster_id``
                        e ``region``).
        cluster_id:     ID do cluster Qdrant Cloud.
        region:         Região (ex: ``"us-east-1"``). Usado somente se ``url``
                        não for fornecida.
        provider:       Cloud provider: ``"aws"``, ``"gcp"``, ``"azure"``.
                        Usado somente se ``url`` não for fornecida.
        api_key:        API key do Qdrant Cloud (obrigatório).
        collections:    Lista de collections. Default: ``["articles","web_cache","search"]``.
        retrieval_mode: ``"hybrid"`` (dense+BM25), ``"dense"``, ``"sparse"``.
                        Default ``"hybrid"``.

    Returns:
        ``{"url": ..., "api_key": ..., "collections": [...], "retrieval_mode": ...}``
    """
    effective_url = url or URL_TEMPLATE.format(
        cluster_id=cluster_id,
        region=region,
    ).replace("aws.", f"{provider}.")

    return {
        "url": effective_url,
        "api_key": api_key,
        "collections": collections or ["articles", "web_cache", "search"],
        "retrieval_mode": retrieval_mode,
    }


def configure_settings(settings: Any, **kwargs: Any) -> None:
    """Aplica flags Qdrant Cloud ao objeto settings.

    Define ``storage_mode = "complete"``, ``qdrant_url`` e ``qdrant_api_key``.
    """
    cfg = build_config(**kwargs)
    settings.storage_mode = "complete"
    settings.qdrant_url = cfg["url"]
    settings.qdrant_api_key = cfg["api_key"]
    logger.info(
        "Qdrant Cloud configurado: storage_mode=complete url=%s…",
        cfg["url"][:40],
    )


async def healthcheck(
    url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Smoke test de conectividade ao Qdrant Cloud.

    Verifica conexão e lista as collections existentes.

    Args:
        url:     URL do cluster. None usa ``settings.qdrant_url``.
        api_key: API key. None usa ``settings.qdrant_api_key``.

    Returns:
        ``{"ok": True, "version": "...", "collections": [...]}``
        ou ``{"ok": False, "error": "..."}``
    """
    try:
        from qdrant_client import QdrantClient

        if url is None or api_key is None:
            from backend.settings import settings as _s

            url = url or _s.qdrant_url
            api_key = api_key or _s.qdrant_api_key

        if not url:
            return {"ok": False, "error": "qdrant_url não configurado"}

        client = QdrantClient(url=url, api_key=api_key)
        info = client.get_collections()
        collections = [c.name for c in info.collections]

        # Versão do servidor (best-effort)
        version = "?"
        try:
            srv_info = client.info()
            version = getattr(srv_info, "version", "?") or "?"
        except Exception:
            pass

        return {"ok": True, "version": version, "collections": collections}

    except Exception as exc:
        logger.debug("Qdrant Cloud healthcheck falhou: %s", exc)
        return {"ok": False, "error": str(exc)}
