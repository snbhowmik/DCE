"""T3.1: in-house yield model (Beta-Binomial failures, empirical yield ratios, aligned paths)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.capacity.inhouse import InHouseConfig, estimate_lines, infer_alignment, simulate_inhouse

W0 = date(2024, 1, 1)


def wk(i: int) -> date:
    return W0 + timedelta(weeks=i)


def batches(n: int, failed_every: int = 0, ratio: float = 0.9, weeks_long: int = 2) -> pl.DataFrame:
    rows = []
    for i in range(n):
        failed = failed_every and i % failed_every == 0
        rows.append(
            {
                "batch_id": f"B{i}", "start_date": wk(i), "end_date": wk(i + weeks_long) - timedelta(days=1),
                "facility_id": "F1", "product_line": "cc", "planned_yield_kg": 100.0,
                "actual_yield_kg": 0.0 if failed else 100.0 * ratio,
                "outcome": "failed" if failed else "success",
            }
        )  # fmt: skip
    return pl.DataFrame(rows)


def plan(n: int, per_week: int = 1, y: float = 100.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "week_start": [wk(i) for i in range(n)],
            "facility_id": ["F1"] * n,
            "product_line": ["cc"] * n,
            "planned_batches": [per_week] * n,
            "planned_yield_per_batch_kg": [y] * n,
        }
    )


H = [wk(104 + h) for h in range(13)]
WINDOW = (W0, wk(103))


def test_beta_posterior_and_ratios() -> None:
    [lp] = estimate_lines(batches(100, failed_every=10), wk(103), InHouseConfig())
    assert (lp.n_batches, lp.n_failed) == (100, 10)
    assert (lp.alpha, lp.beta) == (11.0, 91.0)
    assert lp.ratios.size == 90 and lp.ratios == pytest.approx(0.9)


def test_paths_mean_matches_expectation() -> None:
    cap = simulate_inhouse(
        batches(104, failed_every=10), plan(117, 2), WINDOW, H, n_paths=4000, seed=1
    )
    assert cap.line_paths.shape == (1, 4000, 13)
    assert cap.planned_kg[0] == pytest.approx([200.0] * 13)
    expected = 200 * 0.9 * (1 - cap.params[0].p_fail_mean)
    assert cap.total_paths().mean() == pytest.approx(expected, rel=0.03)
    assert (cap.total_paths() >= 0).all() and (cap.total_paths() <= 200).all()


def test_deterministic_by_seed() -> None:
    a = simulate_inhouse(batches(104, 7), plan(117), WINDOW, H, n_paths=200, seed=3)
    b = simulate_inhouse(batches(104, 7), plan(117), WINDOW, H, n_paths=200, seed=3)
    c = simulate_inhouse(batches(104, 7), plan(117), WINDOW, H, n_paths=200, seed=4)
    assert np.array_equal(a.line_paths, b.line_paths)
    assert not np.array_equal(a.line_paths, c.line_paths)


def test_alignment_inference() -> None:
    # Plan has batches only on even weeks. Batches start on even weeks and end 3 weeks later.
    b = batches(0)
    rows = []
    for i in range(0, 100, 2):
        rows.append({"batch_id": f"B{i}", "start_date": wk(i), "end_date": wk(i + 3), "facility_id": "F1",
                     "product_line": "cc", "planned_yield_kg": 100.0, "actual_yield_kg": 90.0, "outcome": "success"})  # fmt: skip
    b = pl.DataFrame(rows)
    p = plan(100).with_columns(
        pl.when(pl.int_range(pl.len()) % 2 == 0).then(1).otherwise(0).alias("planned_batches")
    )
    align, scores = infer_alignment(b, p, W0, wk(99))
    assert align == "start" and scores["start"] < scores["end"]
    shifted = p.with_columns((pl.col("week_start") - timedelta(weeks=3)).alias("week_start"))
    # now the plan counts output weeks (batches end 3 weeks after start)
    align2, _ = infer_alignment(
        b, shifted.with_columns(pl.col("week_start") + timedelta(weeks=6)), W0, wk(99)
    )
    assert align2 == "end"


def test_start_alignment_shifts_output() -> None:
    cfg = InHouseConfig(plan_alignment="start")
    p = plan(117).with_columns(
        pl.when(pl.col("week_start") == wk(103)).then(5).otherwise(1).alias("planned_batches")
    )
    cap = simulate_inhouse(batches(104, weeks_long=2), p, WINDOW, H, n_paths=10, seed=0, cfg=cfg)
    # 5 batches started in week 103 deliver 2 weeks later → horizon week index 1
    assert cap.planned_kg[0][1] == pytest.approx(500.0)
    assert cap.extra["batch_weeks"] == 2


def test_plan_line_without_history_uses_prior() -> None:
    p = pl.concat([plan(117), plan(117).with_columns(pl.lit("F2").alias("facility_id"))])
    cap = simulate_inhouse(batches(104), p, WINDOW, H, n_paths=50, seed=0)
    assert ("F2", "cc") in cap.line_keys
    prm = cap.params_frame().filter(pl.col("facility_id") == "F2").row(0, named=True)
    assert prm["n_batches"] == 0 and prm["p_fail_mean"] == pytest.approx(0.5)


def test_fixture_runs(tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]) -> None:
    last = tiny_window[1]
    hz = [last + timedelta(weeks=h) for h in range(1, 14)]
    cap = simulate_inhouse(
        tiny_tables["capacity_batches"],
        tiny_tables["capacity_plan"],
        tiny_window,
        hz,
        n_paths=100,
        seed=0,
    )
    assert cap.total_paths().shape == (100, 13)
    assert cap.params[0].n_failed == 1
