"""Durable monthly quota and cost estimation for managed media calls."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from backend.settings import settings

MONTHLY_LIMITS = {"free": 10, "pro": 100}
UNIT_COSTS = {"generate_image": 1, "text_to_speech": 1, "generate_video": 10}
QuotaState = Literal["reserved", "finalized", "failed", "unknown", "cancelled"]


@dataclass(frozen=True)
class QuotaReservation:
    id: str
    user_id: str
    period: str
    operation: str
    units: int


class MediaQuota:
    """SQLite-backed quota repository with idempotent reservations."""

    def __init__(self, database: Path | None = None) -> None:
        self.database = cast(Path, database or settings.db_file)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS media_quota_usage (
              user_id TEXT NOT NULL, period TEXT NOT NULL, used_units INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (user_id, period)
            );
            CREATE TABLE IF NOT EXISTS media_quota_reservations (
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, period TEXT NOT NULL,
              operation TEXT NOT NULL, units INTEGER NOT NULL, state TEXT NOT NULL,
              created_at TEXT NOT NULL, UNIQUE(user_id, period, id)
            );
            """
        )
        return connection

    @staticmethod
    def period() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    async def reserve(
        self, *, user_id: str, operation: str, idempotency_key: str
    ) -> QuotaReservation | None:
        return await asyncio.to_thread(
            self._reserve, user_id, operation, idempotency_key
        )

    def _reserve(
        self, user_id: str, operation: str, idempotency_key: str
    ) -> QuotaReservation | None:
        from backend.rbac.subscription import get_current_tier

        units = UNIT_COSTS.get(operation, 0)
        try:
            tier = get_current_tier(user_id)
        except TypeError:
            tier = get_current_tier() if user_id == "local" else None
        if tier is None:
            return None
        period = self.period()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT user_id, period, operation, units, state FROM media_quota_reservations WHERE id = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing[0] != user_id or existing[2] != operation:
                    return None
                if existing[4] in {"failed", "cancelled"}:
                    updated = db.execute(
                        "UPDATE media_quota_usage SET used_units = used_units + ? "
                        "WHERE user_id = ? AND period = ? AND used_units + ? <= ?",
                        (
                            existing[3],
                            user_id,
                            existing[1],
                            existing[3],
                            MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"]),
                        ),
                    )
                    if updated.rowcount != 1:
                        return None
                    db.execute(
                        "UPDATE media_quota_reservations SET state = 'reserved' WHERE id = ?",
                        (idempotency_key,),
                    )
                return QuotaReservation(
                    idempotency_key, existing[0], existing[1], existing[2], existing[3]
                )
            db.execute(
                "INSERT OR IGNORE INTO media_quota_usage(user_id, period, used_units) VALUES (?, ?, 0)",
                (user_id, period),
            )
            updated = db.execute(
                "UPDATE media_quota_usage SET used_units = used_units + ? "
                "WHERE user_id = ? AND period = ? AND used_units + ? <= ?",
                (
                    units,
                    user_id,
                    period,
                    units,
                    MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"]),
                ),
            )
            if updated.rowcount != 1:
                return None
            db.execute(
                "INSERT INTO media_quota_reservations(id, user_id, period, operation, units, state, created_at) VALUES (?, ?, ?, ?, ?, 'reserved', ?)",
                (
                    idempotency_key,
                    user_id,
                    period,
                    operation,
                    units,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return QuotaReservation(idempotency_key, user_id, period, operation, units)

    async def finalize(
        self, reservation: QuotaReservation, *, state: QuotaState
    ) -> None:
        await asyncio.to_thread(self._finalize, reservation.id, state)

    def _finalize(self, reservation_id: str, state: QuotaState) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT user_id, period, units, state FROM media_quota_reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None or row[3] != "reserved":
                return
            db.execute(
                "UPDATE media_quota_reservations SET state = ? WHERE id = ? AND state = 'reserved'",
                (state, reservation_id),
            )
            if state in {"failed", "cancelled"}:
                db.execute(
                    "UPDATE media_quota_usage SET used_units = MAX(0, used_units - ?) "
                    "WHERE user_id = ? AND period = ?",
                    (row[2], row[0], row[1]),
                )

    async def summary(self, user_id: str) -> dict[str, int | str]:
        return await asyncio.to_thread(self._summary, user_id)

    def _summary(self, user_id: str) -> dict[str, int | str]:
        from backend.rbac.subscription import get_current_tier

        try:
            tier = get_current_tier(user_id)
        except TypeError:
            tier = get_current_tier() if user_id == "local" else None
        if tier is None:
            return {"period": self.period(), "used": 0, "limit": 0, "remaining": 0}
        period = self.period()
        with self._connect() as db:
            row = db.execute(
                "SELECT used_units FROM media_quota_usage WHERE user_id = ? AND period = ?",
                (user_id, period),
            ).fetchone()
        limit = MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"])
        used = int(row[0]) if row else 0
        return {
            "period": period,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }


media_quota = MediaQuota()


def media_estimate(operation: str) -> int:
    """Return the deterministic quota unit estimate for an operation."""
    return UNIT_COSTS.get(operation, 0)


def new_idempotency_key(ctx_call_id: str, operation: str) -> str:
    return ctx_call_id or f"{operation}:{uuid4().hex}"
