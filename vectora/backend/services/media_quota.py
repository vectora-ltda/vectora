"""Durable monthly quota and cost estimation for managed media calls."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from backend.settings import settings

logger = logging.getLogger(__name__)

MONTHLY_LIMITS = {"free": 10, "pro": 100}
UNIT_COSTS = {"generate_image": 1, "text_to_speech": 1, "generate_video": 10}
QuotaState = Literal["reserved", "finalized", "failed", "unknown", "cancelled"]


@dataclass(frozen=True)
class MediaEstimate:
    """Estimativa versionada compartilhada pelo HITL e pela reserva."""

    operation: str
    provider: str
    model: str
    version: str
    billable_unit: str
    currency: str
    units: int


@dataclass(frozen=True)
class QuotaReservation:
    id: str
    user_id: str
    period: str
    operation: str
    units: int
    state: QuotaState = "reserved"


class MediaQuota:
    """SQLite-backed quota repository with idempotent reservations."""

    def __init__(self, database: Path | None = None) -> None:
        self.database = cast("Path", database or settings.db_file)

    @staticmethod
    def _postgres_enabled() -> bool:
        """Indica se o storage completo deve usar Postgres para quota."""
        try:
            from backend.services.license import get_effective_storage_mode

            return get_effective_storage_mode() == "complete" and bool(
                settings.postgres_dsn
            )
        except Exception:
            return False

    async def _postgres_pool(
        self,
    ) -> Any:  # asyncpg é dependência opcional do modo completo
        from backend.storage.factory import get_pg_pool

        return await get_pg_pool()

    @staticmethod
    def _current_tier(user_id: str) -> str | None:
        """Resolve o entitlement no store autoritativo local.

        O Postgres mantém somente o estado operacional da quota. Entitlements
        continuam no SQLite, portanto nunca consultamos uma tabela paralela
        de usuários durante uma reserva gerenciada.
        """
        from backend.rbac.subscription import get_current_tier

        try:
            tier = get_current_tier(user_id)
        except TypeError:
            tier = get_current_tier() if user_id == "local" else None
        return tier if tier in MONTHLY_LIMITS else None

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _record_transition(
        *,
        operation: str,
        units: int,
        previous_state: str | None,
        new_state: str,
    ) -> None:
        """Registra uma transição somente depois de a transação confirmar."""
        try:
            from backend.persistence.telemetry import telemetry

            telemetry.record_media_quota(
                "transition",
                operation=operation,
                units=units,
                previous_state=previous_state,
                new_state=new_state,
            )
        except Exception:
            logger.debug("media_quota: falha ao registrar transição", exc_info=True)

    @staticmethod
    def period() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    async def reserve(
        self,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        units: int | None = None,
    ) -> QuotaReservation | None:
        if self._postgres_enabled():
            return await self._reserve_postgres(
                user_id, operation, idempotency_key, units
            )
        return await asyncio.to_thread(
            self._reserve, user_id, operation, idempotency_key, units
        )

    async def _reserve_postgres(  # noqa: PLR0911
        self, user_id: str, operation: str, idempotency_key: str, units: int | None
    ) -> QuotaReservation | None:
        estimate_units = UNIT_COSTS.get(operation, 0) if units is None else units
        tier = self._current_tier(user_id)
        if tier is None:
            logger.info(
                "media_quota.blocked",
                extra={"operation": operation, "reason": "entitlement_unavailable"},
            )
            return None
        pool = await self._postgres_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                period = self.period()
                # A chave idempotente é o primeiro recurso serializado. O
                # índice PRIMARY KEY faz concorrentes com a mesma chave
                # esperarem pela primeira transação, evitando que ambas
                # debitem a quota antes de descobrir o conflito.
                inserted = await connection.fetchrow(
                    "INSERT INTO media_quota_reservations "
                    "(id, user_id, period, operation, units, state) "
                    "VALUES ($1, $2, $3, $4, $5, 'reserved') "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id",
                    idempotency_key,
                    user_id,
                    period,
                    operation,
                    estimate_units,
                )
                existing = None
                if inserted is None:
                    existing = await connection.fetchrow(
                        "SELECT user_id, period, operation, units, state "
                        "FROM media_quota_reservations WHERE id = $1 FOR UPDATE",
                        idempotency_key,
                    )
                if existing is not None:
                    if (
                        existing["user_id"] != user_id
                        or existing["operation"] != operation
                    ):
                        return None
                    if existing["state"] in {"failed", "cancelled"}:
                        await connection.execute(
                            "INSERT INTO media_quota_usage(user_id, period, used_units) "
                            "VALUES ($1, $2, 0) ON CONFLICT (user_id, period) DO NOTHING",
                            user_id,
                            period,
                        )
                        updated = await connection.execute(
                            "UPDATE media_quota_usage SET used_units = used_units + $1 "
                            "WHERE user_id = $2 AND period = $3 AND used_units + $1 <= $4",
                            existing["units"],
                            user_id,
                            period,
                            MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"]),
                        )
                        if not updated.endswith("1"):
                            return None
                        await connection.execute(
                            "UPDATE media_quota_reservations SET state = 'reserved', period = $1 "
                            "WHERE id = $2",
                            period,
                            idempotency_key,
                        )
                        return QuotaReservation(
                            idempotency_key,
                            user_id,
                            period,
                            operation,
                            existing["units"],
                            "reserved",
                        )
                    return QuotaReservation(
                        idempotency_key,
                        existing["user_id"],
                        existing["period"],
                        existing["operation"],
                        existing["units"],
                        existing["state"],
                    )
                await connection.execute(
                    "INSERT INTO media_quota_usage(user_id, period, used_units) "
                    "VALUES ($1, $2, 0) ON CONFLICT (user_id, period) DO NOTHING",
                    user_id,
                    period,
                )
                updated = await connection.execute(
                    "UPDATE media_quota_usage SET used_units = used_units + $1 "
                    "WHERE user_id = $2 AND period = $3 AND used_units + $1 <= $4",
                    estimate_units,
                    user_id,
                    period,
                    MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"]),
                )
                if not updated.endswith("1"):
                    await connection.execute(
                        "DELETE FROM media_quota_reservations WHERE id = $1",
                        idempotency_key,
                    )
                    return None
                # A reserva foi inserida acima; só o débito ainda precisa ser
                # confirmado nesta mesma transação.
        self._record_transition(
            operation=operation,
            units=estimate_units,
            previous_state=None,
            new_state="reserved",
        )
        return QuotaReservation(
            idempotency_key, user_id, period, operation, estimate_units
        )

    def _reserve(
        self,
        user_id: str,
        operation: str,
        idempotency_key: str,
        units: int | None = None,
    ) -> QuotaReservation | None:
        from backend.rbac.subscription import get_current_tier

        units = UNIT_COSTS.get(operation, 0) if units is None else units
        logger.info(
            "media_quota.estimate",
            extra={
                "operation": operation,
                "units": units,
                "estimate_version": "v1",
                "currency": "quota_units",
            },
        )
        try:
            tier = get_current_tier(user_id)
        except TypeError:
            tier = get_current_tier() if user_id == "local" else None
        if tier is None:
            logger.info(
                "media_quota.blocked",
                extra={"operation": operation, "reason": "entitlement_unavailable"},
            )
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
                    logger.info(
                        "media_quota.blocked",
                        extra={
                            "operation": operation,
                            "reason": "idempotency_conflict",
                        },
                    )
                    return None
                if existing[4] in {"failed", "cancelled"}:
                    previous_state = existing[4]
                    retry_period = period
                    db.execute(
                        "INSERT OR IGNORE INTO media_quota_usage(user_id, period, used_units) VALUES (?, ?, 0)",
                        (user_id, retry_period),
                    )
                    updated = db.execute(
                        "UPDATE media_quota_usage SET used_units = used_units + ? "
                        "WHERE user_id = ? AND period = ? AND used_units + ? <= ?",
                        (
                            existing[3],
                            user_id,
                            retry_period,
                            existing[3],
                            MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"]),
                        ),
                    )
                    if updated.rowcount != 1:
                        logger.info(
                            "media_quota.blocked",
                            extra={"operation": operation, "reason": "limit_exceeded"},
                        )
                        return None
                    db.execute(
                        "UPDATE media_quota_reservations SET state = 'reserved', period = ? WHERE id = ?",
                        (retry_period, idempotency_key),
                    )
                    existing = (
                        existing[0],
                        retry_period,
                        existing[2],
                        existing[3],
                        "reserved",
                    )
                    transition = (existing[2], existing[3], previous_state)
                else:
                    transition = None
                result = QuotaReservation(
                    idempotency_key,
                    existing[0],
                    existing[1],
                    existing[2],
                    existing[3],
                    cast("QuotaState", existing[4]),
                )
                if transition is not None:
                    db.commit()
                    self._record_transition(
                        operation=transition[0],
                        units=transition[1],
                        previous_state=transition[2],
                        new_state="reserved",
                    )
                return result
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
                logger.info(
                    "media_quota.blocked",
                    extra={"operation": operation, "reason": "limit_exceeded"},
                )
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
        self._record_transition(
            operation=operation,
            units=units,
            previous_state=None,
            new_state="reserved",
        )
        logger.info(
            "media_quota.reserved", extra={"operation": operation, "units": units}
        )
        return QuotaReservation(idempotency_key, user_id, period, operation, units)

    async def finalize(
        self, reservation: QuotaReservation, *, state: QuotaState
    ) -> None:
        if self._postgres_enabled():
            await self._finalize_postgres(reservation.id, state)
        else:
            await asyncio.to_thread(self._finalize, reservation.id, state)

    async def _finalize_postgres(self, reservation_id: str, state: QuotaState) -> None:
        pool = await self._postgres_pool()
        transition: tuple[str, int] | None = None
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT user_id, period, operation, units FROM media_quota_reservations "
                    "WHERE id = $1 AND state = 'reserved' FOR UPDATE",
                    reservation_id,
                )
                if row is None:
                    return
                await connection.execute(
                    "UPDATE media_quota_reservations SET state = $1 WHERE id = $2",
                    state,
                    reservation_id,
                )
                if state in {"failed", "cancelled"}:
                    await connection.execute(
                        "UPDATE media_quota_usage SET used_units = GREATEST(0, used_units - $1) "
                        "WHERE user_id = $2 AND period = $3",
                        row["units"],
                        row["user_id"],
                        row["period"],
                    )
                transition = (row["operation"], int(row["units"]))
        if transition is not None:
            self._record_transition(
                operation=transition[0],
                units=transition[1],
                previous_state="reserved",
                new_state=state,
            )

    def _finalize(self, reservation_id: str, state: QuotaState) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT user_id, period, operation, units, state FROM media_quota_reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None or row[4] != "reserved":
                return
            updated = db.execute(
                "UPDATE media_quota_reservations SET state = ? WHERE id = ? AND state = 'reserved'",
                (state, reservation_id),
            )
            if updated.rowcount == 1:
                db.commit()
                self._record_transition(
                    operation=row[2],
                    units=row[3],
                    previous_state="reserved",
                    new_state=state,
                )
                logger.info("media_quota.finalized", extra={"state": state})
            if updated.rowcount == 1 and state in {"failed", "cancelled"}:
                db.execute(
                    "UPDATE media_quota_usage SET used_units = MAX(0, used_units - ?) "
                    "WHERE user_id = ? AND period = ?",
                    (row[3], row[0], row[1]),
                )

    async def summary(self, user_id: str) -> dict[str, int | str]:
        if self._postgres_enabled():
            return await self._summary_postgres(user_id)
        return await asyncio.to_thread(self._summary, user_id)

    async def _summary_postgres(self, user_id: str) -> dict[str, int | str]:
        tier = self._current_tier(user_id)
        period = self.period()
        if tier is None:
            return {"period": period, "used": 0, "limit": 0, "remaining": 0}
        pool = await self._postgres_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT used_units FROM media_quota_usage WHERE user_id = $1 AND period = $2",
                user_id,
                period,
            )
        limit = MONTHLY_LIMITS.get(tier, MONTHLY_LIMITS["free"])
        used = int(row["used_units"]) if row else 0
        return {
            "period": period,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }

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
        result = {
            "period": period,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }
        logger.info(
            "media_quota.summary",
            extra={
                "period": period,
                "used": used,
                "limit": limit,
                "remaining": result["remaining"],
            },
        )
        return result


media_quota = MediaQuota()


def media_estimate_record(
    operation: str, *, provider: str = "", model: str = ""
) -> MediaEstimate:
    """Retorna a estimativa canônica usada antes e durante a execução.

    A versão e a unidade fazem parte do contrato para que mudanças de preço
    não alterem silenciosamente uma aprovação já apresentada ao usuário.
    """
    return MediaEstimate(
        operation=operation,
        provider=provider,
        model=model,
        version="v1",
        billable_unit="quota_unit",
        currency="quota_units",
        units=UNIT_COSTS.get(operation, 0),
    )


def media_estimate(operation: str, *, provider: str = "", model: str = "") -> int:
    """Retorna as unidades da estimativa canônica de uma operação."""
    return media_estimate_record(operation, provider=provider, model=model).units


def new_idempotency_key(ctx_call_id: str, operation: str) -> str:
    key = ctx_call_id.strip()
    if not key:
        raise ValueError(f"identidade estável ausente para {operation}")
    return key
