"""Capacity forecast: in-house quantiles + paths, co-man partners, perishability (ARCH §5.5, T3.3).

Strategy-mode agnostic (IDEATION P1): the optimizer picks the capacity quantile, not this module.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from dce.capacity.coman import CoManPartner, load_partners
from dce.capacity.inhouse import InHouseCapacity, InHouseConfig, simulate_inhouse
from dce.scope import production_line

QUANTILES = (0.1, 0.5, 0.9)


def carryover_weeks(shelf_life_days: int, cap_weeks: int) -> int:
    """ARCH §5.5: no carryover under 7 days of shelf life, else ⌊shelf_life/7⌋ capped by config."""
    if shelf_life_days < 7:
        return 0
    return int(min(shelf_life_days // 7, cap_weeks))


@dataclass(frozen=True)
class Perishability:
    shelf_life_days: int
    carryover_weeks: int
    waste_cost_inr_per_kg: float


@dataclass
class CapacityForecast:
    horizon: list[date]
    product_line: str
    inhouse: InHouseCapacity
    partners: list[CoManPartner]
    perishability: Perishability
    paths: np.ndarray  # [P, H] in-house kg for `product_line`
    quantiles: pl.DataFrame  # week_start, h, planned_kg, mean, q10, q50, q90

    def at_quantile(self, q: float) -> np.ndarray:
        """In-house capacity per horizon week at quantile q (e.g. a mode's q_capacity)."""
        return np.quantile(self.paths, q, axis=0)

    def artifact_hash(self) -> str:
        h = hashlib.sha256()
        buf = io.BytesIO()
        self.quantiles.write_csv(buf, float_precision=10)
        h.update(buf.getvalue())
        h.update(np.ascontiguousarray(np.round(self.paths, 10)).tobytes())
        h.update(repr(self.perishability).encode())
        for p in self.partners:
            h.update(repr(sorted(p.summary().items(), key=lambda kv: kv[0])).encode())
        return h.hexdigest()

    def write(self, out: Path) -> dict[str, Path]:
        out.mkdir(parents=True, exist_ok=True)
        files = {
            "capacity_quantiles": out / "capacity_quantiles.parquet",
            "capacity_paths": out / "capacity_paths.parquet",
            "capacity_params": out / "capacity_params.parquet",
            "coman_partners": out / "coman_partners.parquet",
        }
        self.quantiles.write_parquet(files["capacity_quantiles"])
        P, H = self.paths.shape
        pl.DataFrame(
            {
                "path": np.repeat(np.arange(P), H),
                "week_start": self.horizon * P,
                "value": self.paths.ravel(),
            }
        ).write_parquet(files["capacity_paths"])
        self.inhouse.params_frame().write_parquet(files["capacity_params"])
        pl.DataFrame([p.summary() for p in self.partners]).write_parquet(files["coman_partners"])
        return files


def capacity_quantiles(paths: np.ndarray, horizon: list[date], planned: np.ndarray) -> pl.DataFrame:
    q = np.quantile(paths, QUANTILES, axis=0)
    return pl.DataFrame(
        {
            "week_start": horizon,
            "h": list(range(1, len(horizon) + 1)),
            "planned_kg": planned,
            "mean": paths.mean(axis=0),
            "q10": q[0],
            "q50": q[1],
            "q90": q[2],
        }
    )


def capacity_forecast(
    tables: dict[str, pl.DataFrame],
    window: tuple[date, date],
    app_cfg: dict[str, Any],
    seed: int,
) -> CapacityForecast:
    _, last = window
    H = int(app_cfg.get("horizon_weeks", 13))
    n_paths = int(app_cfg.get("n_paths", 500))
    cap_cfg = app_cfg.get("capacity", {})
    horizon = [last + timedelta(weeks=h) for h in range(1, H + 1)]
    line = production_line(tables["skus"])

    inhouse = simulate_inhouse(
        tables["capacity_batches"],
        tables["capacity_plan"],
        window,
        horizon,
        n_paths=n_paths,
        seed=seed,
        cfg=InHouseConfig.from_app(app_cfg),
    )
    paths = inhouse.total_paths(line)
    idx = [i for i, (_, pl_) in enumerate(inhouse.line_keys) if pl_ == line]
    planned = inhouse.planned_kg[idx].sum(axis=0) if idx else np.zeros(H)

    prior = cap_cfg.get("coman_reliability_prior", (9.0, 1.0))
    partners = [
        p
        for p in load_partners(
            tables["coman_contracts"], tables["coman_activity"], (float(prior[0]), float(prior[1]))
        )
        if p.product_line == line
    ]
    skus = tables["skus"].filter(
        (pl.col("status") == "production") & (pl.col("product_line") == line)
    )
    shelf = int(skus["shelf_life_days"].min())  # type: ignore[arg-type]
    waste = cap_cfg.get("waste_cost_inr_per_kg")
    perish = Perishability(
        shelf_life_days=shelf,
        carryover_weeks=carryover_weeks(shelf, int(cap_cfg.get("carryover_cap_weeks", 1))),
        waste_cost_inr_per_kg=float(
            waste if waste is not None else skus["unit_cost_inr_per_kg"].mean()  # type: ignore[arg-type]
        ),
    )
    return CapacityForecast(
        horizon=horizon,
        product_line=line,
        inhouse=inhouse,
        partners=partners,
        perishability=perish,
        paths=paths,
        quantiles=capacity_quantiles(paths, horizon, planned),
    )
