"""P0 unit tests for pure-math helpers in qd_ingest.sources.derived.

Covers:
- _third_wednesday (U-020, U-021)
- _bs_price (U-022, U-023)  including put-call parity
- _bs_iv (U-024, U-025, U-026)  bisection round-trip
"""
from __future__ import annotations

import datetime as dt
import math

import pytest

from qd_ingest.sources.derived import _bs_iv, _bs_price, _third_wednesday


# ── _third_wednesday ────────────────────────────────────────────────────────

def test_U020_third_wednesday_2025_january():
    # 2025-01-15 is the third Wednesday of January 2025
    assert _third_wednesday(2025, 1) == dt.date(2025, 1, 15)


@pytest.mark.parametrize("y, m, expected", [
    (2024, 1, dt.date(2024, 1, 17)),
    (2024, 2, dt.date(2024, 2, 21)),
    (2024, 7, dt.date(2024, 7, 17)),
    (2024, 12, dt.date(2024, 12, 18)),
    (2025, 5, dt.date(2025, 5, 21)),
    (2025, 11, dt.date(2025, 11, 19)),
    (2026, 1, dt.date(2026, 1, 21)),
    (2026, 12, dt.date(2026, 12, 16)),
])
def test_U021_third_wednesday_spot_check(y, m, expected):
    assert _third_wednesday(y, m) == expected


def test_U021_third_wednesday_is_always_wednesday():
    # Wed.weekday() == 2 for every output, 36 months sample
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            d = _third_wednesday(y, m)
            assert d.weekday() == 2, f"{y}-{m}: {d} is {d.strftime('%A')}"
            assert 15 <= d.day <= 21, f"{y}-{m}: {d.day} not in 15..21"


# ── _bs_price ───────────────────────────────────────────────────────────────

def test_U022_bs_call_atm_known_value():
    # ATM call: S=K=100, σ=20%, T=30/365, rf=1.5% (module constant)
    # Reference value computed with same formula, fixed expected to ±1e-3.
    price = _bs_price(S=100.0, K=100.0, T=30 / 365, sigma=0.20, is_call=True)
    assert 2.0 < price < 3.0
    # exact round-trip with _bs_iv must hold (proxy correctness)
    iv = _bs_iv(price, S=100.0, K=100.0, T=30 / 365, is_call=True)
    assert abs(iv - 0.20) < 1e-3


def test_U023_put_call_parity():
    # C - P = S - K * exp(-rf * T)   with rf=0.015 (module constant)
    S, K, T = 100.0, 100.0, 30 / 365
    sigma = 0.25
    c = _bs_price(S, K, T, sigma, is_call=True)
    p = _bs_price(S, K, T, sigma, is_call=False)
    rhs = S - K * math.exp(-0.015 * T)
    assert abs((c - p) - rhs) < 1e-6, f"parity broken: C-P={c-p:.6f}, S-Kexp={rhs:.6f}"


def test_U023_bs_at_expiry_returns_intrinsic():
    # T=0 → return intrinsic value (per implementation guard)
    assert _bs_price(S=120, K=100, T=0, sigma=0.2, is_call=True) == 20.0
    assert _bs_price(S=80, K=100, T=0, sigma=0.2, is_call=True) == 0.0
    assert _bs_price(S=120, K=100, T=0, sigma=0.2, is_call=False) == 0.0
    assert _bs_price(S=80, K=100, T=0, sigma=0.2, is_call=False) == 20.0


# ── _bs_iv ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sigma_in, is_call", [
    (0.10, True), (0.20, True), (0.35, True), (0.60, True),
    (0.15, False), (0.25, False), (0.40, False),
])
def test_U024_bs_iv_round_trip(sigma_in, is_call):
    """Plug sigma into _bs_price, feed result back into _bs_iv, expect original sigma."""
    S, K, T = 100.0, 100.0, 30 / 365
    price = _bs_price(S, K, T, sigma_in, is_call)
    recovered = _bs_iv(price, S, K, T, is_call)
    assert abs(recovered - sigma_in) < 1e-3, \
        f"is_call={is_call} sigma_in={sigma_in} → {recovered}"


def test_U025_bs_iv_above_max_modelable_returns_nan():
    # Price above what σ=5.0 (the upper bracket) can model → NaN guard
    # Use an absurdly high price (much greater than S itself) to trigger
    # the `_bs_price(S, K, T, hi=5.0, ...) < price` branch.
    iv = _bs_iv(price=1_000_000.0, S=100, K=100, T=30 / 365, is_call=True)
    assert math.isnan(iv), f"got iv={iv}"


def test_U025_bs_iv_price_below_intrinsic_returns_nan():
    # Call price below intrinsic (S=120, K=100, intrinsic=20, but price=5) → invalid
    iv = _bs_iv(price=5.0, S=120, K=100, T=30 / 365, is_call=True)
    assert math.isnan(iv)


def test_U025_bs_iv_none_or_zero_price_returns_nan():
    # Guards against missing data
    assert math.isnan(_bs_iv(price=None, S=100, K=100, T=30 / 365, is_call=True))
    assert math.isnan(_bs_iv(price=0.0, S=100, K=100, T=30 / 365, is_call=True))


def test_U026_bs_iv_converges_within_60_iter():
    # Iteration count not exposed; smoke that result is precise enough that
    # 60 bisection steps must have been sufficient (each halves the interval,
    # 60 iter → ~5×0.0001 precision on [1e-4, 5.0] domain).
    sigma_in = 0.45
    price = _bs_price(100, 100, 90 / 365, sigma_in, True)
    iv = _bs_iv(price, 100, 100, 90 / 365, True)
    # Bisection: width / 2^60 ≈ 5e-18 → bracket error ≈ 1e-16, but our tolerance
    # is set by the function's mid-of-bracket return → at most width / 2^61
    assert abs(iv - sigma_in) < 1e-6
