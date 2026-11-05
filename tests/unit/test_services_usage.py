"""Tests para src/services/usage.py — rastreador de uso por usuário (R5).

Janela deslizante em memória: conta requests por user_id dentro de uma janela
de tempo, expondo quanto foi consumido e quando a janela reseta. Alimenta o
medidor de uso do plano (GET /auth/usage).
"""

from __future__ import annotations

from backend.services.usage import UsageTracker

# ---------------------------------------------------------------------------
# Contagem básica
# ---------------------------------------------------------------------------


def test_empty_usage_is_zero():
    t = UsageTracker(limit=60, window_seconds=60)
    u = t.usage("user1", now=1000.0)
    assert u["used"] == 0
    assert u["limit"] == 60
    assert u["remaining"] == 60


def test_record_increments_used():
    t = UsageTracker(limit=60, window_seconds=60)
    t.record("user1", now=1000.0)
    t.record("user1", now=1001.0)
    u = t.usage("user1", now=1002.0)
    assert u["used"] == 2
    assert u["remaining"] == 58


# ---------------------------------------------------------------------------
# Isolamento por usuário
# ---------------------------------------------------------------------------


def test_users_are_isolated():
    t = UsageTracker(limit=60, window_seconds=60)
    t.record("a", now=1000.0)
    t.record("a", now=1000.5)
    t.record("b", now=1000.0)
    assert t.usage("a", now=1001.0)["used"] == 2
    assert t.usage("b", now=1001.0)["used"] == 1


# ---------------------------------------------------------------------------
# Janela deslizante
# ---------------------------------------------------------------------------


def test_old_events_drop_out_of_window():
    t = UsageTracker(limit=60, window_seconds=60)
    t.record("u", now=1000.0)
    t.record("u", now=1030.0)
    # Em t=1065 a janela cobre [1005, 1065]: o evento de 1000 saiu, o de 1030 fica.
    u = t.usage("u", now=1065.0)
    assert u["used"] == 1


def test_all_events_expire():
    t = UsageTracker(limit=60, window_seconds=60)
    t.record("u", now=1000.0)
    u = t.usage("u", now=2000.0)
    assert u["used"] == 0
    assert u["remaining"] == 60


# ---------------------------------------------------------------------------
# Limite e reset
# ---------------------------------------------------------------------------


def test_remaining_never_negative():
    t = UsageTracker(limit=2, window_seconds=60)
    for i in range(5):
        t.record("u", now=1000.0 + i)
    u = t.usage("u", now=1005.0)
    assert u["used"] == 5
    assert u["remaining"] == 0


def test_reset_in_seconds_counts_down_from_oldest():
    t = UsageTracker(limit=60, window_seconds=60)
    t.record("u", now=1000.0)
    # 20s depois → faltam 40s para o evento mais antigo sair da janela
    u = t.usage("u", now=1020.0)
    assert u["reset_in_seconds"] == 40.0


def test_reset_in_seconds_zero_when_empty():
    t = UsageTracker(limit=60, window_seconds=60)
    u = t.usage("u", now=1000.0)
    assert u["reset_in_seconds"] == 0.0


def test_window_seconds_reported():
    t = UsageTracker(limit=10, window_seconds=120)
    u = t.usage("u", now=1.0)
    assert u["window_seconds"] == 120
