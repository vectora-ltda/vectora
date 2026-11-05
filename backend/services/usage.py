"""Rastreador de uso por usuário — janelas deslizantes em memória.

Conta requests por ``user_id`` dentro de múltiplas janelas de tempo simultâneas
(curta, 5h e semanal) e expõe o consumo para o medidor de uso do plano
(``GET /auth/usage``). Single-process: o estado vive na memória do servidor e
zera ao reiniciar — suficiente para o feedback do chat. Multi-server exigiria
backend compartilhado (Redis), fora do escopo atual.
"""

from __future__ import annotations

import time

__all__ = ["UsageTracker", "usage_tracker"]


_FIVE_HOURS = 5 * 60 * 60
_WEEK = 7 * 24 * 60 * 60


class UsageTracker:
    """Conta eventos por usuário em janelas deslizantes (curta, 5h, semanal).

    A janela curta serve para rate limiting fino. As janelas de 5h e semanal
    alimentam o painel de uso no estilo Claude Code (``Limite de 5 horas`` e
    ``Semanal · todos os modelos``). Limites são configuráveis no construtor.
    """

    def __init__(
        self,
        limit: int = 60,
        window_seconds: int = 60,
        *,
        five_hour_limit: int = 1000,
        weekly_limit: int = 5000,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._five_hour_limit = five_hour_limit
        self._weekly_limit = weekly_limit
        # Lista única de timestamps por usuário — janelas maiores são
        # derivadas dela. Mantém o cap em 1 semana para não inflar memória.
        self._events: dict[str, list[float]] = {}

    @staticmethod
    def _prune_below(bucket: list[float], cutoff: float) -> None:
        """Remove timestamps menores que ``cutoff`` do início da lista."""
        idx = 0
        for ts in bucket:
            if ts >= cutoff:
                break
            idx += 1
        if idx:
            del bucket[:idx]

    def record(self, user_id: str, *, now: float | None = None) -> None:
        """Registra um request do usuário no instante ``now`` (default: agora)."""
        ts = time.time() if now is None else now
        bucket = self._events.setdefault(user_id, [])
        bucket.append(ts)
        # Mantém só o necessário para a maior janela (semanal).
        self._prune_below(bucket, ts - _WEEK)

    def _window_stats(
        self, bucket: list[float], now: float, window: int, limit: int
    ) -> dict[str, float | int]:
        cutoff = now - window
        used = sum(1 for ts in bucket if ts >= cutoff)
        # Tempo até o evento mais antigo dentro da janela sair dela.
        in_window = [ts for ts in bucket if ts >= cutoff]
        reset_in = max(0.0, window - (now - in_window[0])) if in_window else 0.0
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "window_seconds": window,
            "reset_in_seconds": round(reset_in, 1),
        }

    def usage(self, user_id: str, *, now: float | None = None) -> dict[str, object]:
        """Retorna o consumo atual nas três janelas mais um snapshot legado.

        - ``five_hour`` e ``weekly``: alimentam o popover de uso.
        - Campos top-level ``used``/``limit``/...: compatibilidade com o
          consumidor que olha apenas a janela curta.
        """
        ts = time.time() if now is None else now
        bucket = self._events.get(user_id, [])
        self._prune_below(bucket, ts - _WEEK)

        short = self._window_stats(bucket, ts, self._window, self._limit)
        five_hour = self._window_stats(bucket, ts, _FIVE_HOURS, self._five_hour_limit)
        weekly = self._window_stats(bucket, ts, _WEEK, self._weekly_limit)

        return {
            **short,
            "five_hour": five_hour,
            "weekly": weekly,
        }


#: Instância global compartilhada pelo servidor.
usage_tracker = UsageTracker()
