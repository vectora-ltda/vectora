import asyncio

import pytest

from backend.services.media_quota import MediaQuota


@pytest.mark.asyncio
async def test_reserve_is_idempotent_and_summary_is_durable(tmp_path) -> None:
    quota = MediaQuota(tmp_path / "quota.sqlite3")

    first = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="call-1"
    )
    second = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="call-1"
    )

    assert first == second
    assert (await quota.summary("u1"))["used"] == 1


@pytest.mark.asyncio
async def test_reserve_rejects_when_monthly_limit_is_exceeded(tmp_path) -> None:
    quota = MediaQuota(tmp_path / "quota.sqlite3")
    for index in range(10):
        assert await quota.reserve(
            user_id="u1",
            operation="generate_image",
            idempotency_key=f"call-{index}",
        )

    assert (
        await quota.reserve(
            user_id="u1",
            operation="generate_image",
            idempotency_key="call-over-limit",
        )
        is None
    )


@pytest.mark.asyncio
async def test_reservas_concorrentes_nao_ultrapassam_o_limite(tmp_path) -> None:
    quota = MediaQuota(tmp_path / "quota.sqlite3")

    results = await asyncio.gather(
        *(
            quota.reserve(
                user_id="u1",
                operation="generate_image",
                idempotency_key=f"parallel-{index}",
            )
            for index in range(12)
        )
    )

    assert sum(result is not None for result in results) == 10
    assert (await quota.summary("u1"))["used"] == 10
