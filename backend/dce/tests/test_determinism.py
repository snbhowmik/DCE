"""NFR-2 regression: model inputs are bit-identical across repeated runs.

Polars' parallel float sums can differ in the last bit between runs; aggregations are rounded
(`dce.numerics.SUM_DECIMALS`). Allocator churn between runs used to expose the difference.
"""

from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import polars as pl

from dce.config import load_scoring_config
from dce.demand.reconstruct import weekly_demand
from dce.response.run import weekly_region_inputs


def _h(df: pl.DataFrame) -> str:
    return hashlib.sha256(df.write_csv(float_precision=17).encode()).hexdigest()


def test_model_inputs_bit_identical_under_allocator_churn(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    scoring = load_scoring_config()
    rng = np.random.default_rng(0)
    junk = []
    seen_demand, seen_inputs = set(), set()
    for _ in range(40):
        junk.append(np.empty(int(rng.integers(1, 5000)), dtype=np.uint8))
        seen_demand.add(_h(weekly_demand(tiny_tables["orders"], tiny_window[1])))
        seen_inputs.add(_h(weekly_region_inputs(tiny_tables, tiny_window, scoring)))
    assert len(seen_demand) == 1 and len(seen_inputs) == 1
