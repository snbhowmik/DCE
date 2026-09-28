"""T3.2: co-man timing, bounds, and reliability haircut."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.capacity.coman import CoManPartner, load_partners

D = date.fromisoformat


def contracts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "coman_id": ["A", "B"],
            "product_line": ["cc", "cc"],
            "available_from": [D("2024-06-05"), D("2026-09-01")],
            "lead_time_weeks": [4, 6],
            "min_commit_kg_per_week": [50.0, 100.0],
            "max_kg_per_week": [200.0, 300.0],
            "unit_cost_inr_per_kg": [1200.0, 1150.0],
            "min_active_weeks": [4, 8],
        }
    )


def activity() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "coman_id": ["A"] * 4,
            "week_start": [D("2025-01-06") + timedelta(weeks=i) for i in range(4)],
            "requested_kg": [100.0, 100.0, 100.0, 0.0],
            "delivered_kg": [80.0, 100.0, 120.0, 0.0],  # over-delivery capped at 1; 0/0 ignored
        }
    )


@pytest.fixture
def partners() -> dict[str, CoManPartner]:
    return {p.coman_id: p for p in load_partners(contracts(), activity())}


def test_lead_time_and_availability(partners: dict[str, CoManPartner]) -> None:
    a, b = partners["A"], partners["B"]
    decision = D("2026-02-09")
    assert a.earliest_activation(decision) == decision
    assert a.earliest_output(decision) == decision + timedelta(weeks=4)
    # B is not available until 2026-09-01 (a Tuesday → week of 2026-08-31)
    assert b.earliest_activation(decision) == D("2026-08-31")
    assert b.earliest_output(decision) == D("2026-08-31") + timedelta(weeks=6)
    horizon = [decision + timedelta(weeks=h) for h in range(13)]
    assert a.output_mask(horizon, decision).tolist() == [False] * 4 + [True] * 9
    assert not b.output_mask(horizon, decision).any()


def test_volume_bounds(partners: dict[str, CoManPartner]) -> None:
    a = partners["A"]
    assert a.feasible_volume(0) == 0 and a.feasible_volume(-5) == 0
    assert a.feasible_volume(10) == 50 and a.feasible_volume(120) == 120
    assert a.feasible_volume(999) == 200


def test_reliability_haircut_from_history(partners: dict[str, CoManPartner]) -> None:
    a = partners["A"]
    assert sorted(a.reliability.tolist()) == [0.8, 1.0, 1.0]
    assert a.reliability_mean == pytest.approx(2.8 / 3)
    req = np.full(13, 100.0)
    d = a.delivered_paths(req, 5000, seed=1)
    assert d.shape == (5000, 13) and (d <= 100).all()
    assert d.mean() == pytest.approx(100 * 2.8 / 3, rel=0.02)
    assert np.array_equal(d, a.delivered_paths(req, 5000, seed=1))


def test_prior_when_no_history(partners: dict[str, CoManPartner]) -> None:
    b = partners["B"]
    assert b.reliability.size == 0 and b.reliability_mean == pytest.approx(0.9)
    d = b.delivered_paths(np.full(13, 100.0), 5000, seed=2)
    assert d.mean() == pytest.approx(90, rel=0.02)
    assert b.summary()["reliability_from_prior"] is True


def test_fixture(tiny_tables: dict[str, pl.DataFrame]) -> None:
    ps = load_partners(tiny_tables["coman_contracts"], tiny_tables["coman_activity"])
    assert [p.coman_id for p in ps] == ["CM_A", "CM_B"]
    assert ps[0].reliability.size == 8 and ps[1].reliability.size == 0
