"""In-house capacity from batch history (ARCH §5.5, PRD FR-11, T3.1).

Per facility × product line:
  p_fail ~ Beta(a0 + failed, b0 + non-failed)            one draw per path (parameter uncertainty)
  ratio  ~ empirical actual/planned of non-failed batches (bootstrap)
  weekly output = Σ_batches planned_yield × ratio × Bernoulli(1 − p_fail)

`capacity_plan` weeks are interpreted as the batches' output week or start week, inferred from
history (whichever matches the realized batch counts better; ties → output week).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
import polars as pl
from scipy.stats import beta as beta_dist

from dce.seeds import rng as seeded_rng

Alignment = Literal["start", "end"]


@dataclass(frozen=True)
class InHouseConfig:
    failure_prior: tuple[float, float] = (1.0, 1.0)
    history_weeks: int = 104
    plan_alignment: str = "auto"

    @classmethod
    def from_app(cls, app: dict[str, Any]) -> InHouseConfig:
        c = app.get("capacity", {})
        d = cls()
        return cls(
            failure_prior=(
                float(c.get("failure_prior", d.failure_prior)[0]),
                float(c.get("failure_prior", d.failure_prior)[1]),
            ),
            history_weeks=int(c.get("history_weeks", d.history_weeks)),
            plan_alignment=str(c.get("plan_alignment", d.plan_alignment)),
        )


@dataclass
class LineParams:
    facility_id: str
    product_line: str
    n_batches: int
    n_failed: int
    alpha: float
    beta: float
    ratios: np.ndarray  # empirical yield ratios of non-failed batches

    @property
    def p_fail_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass
class InHouseCapacity:
    horizon: list[date]
    alignment: Alignment
    alignment_scores: dict[str, float]
    params: list[LineParams]
    line_keys: list[tuple[str, str]]  # (facility_id, product_line) order of `line_paths`
    line_paths: np.ndarray  # [L, P, H] kg
    planned_kg: np.ndarray  # [L, H] planned (nominal) kg
    extra: dict[str, Any] = field(default_factory=dict)

    def total_paths(self, product_line: str | None = None) -> np.ndarray:
        """[P, H] summed over facilities (optionally one product line)."""
        idx = [i for i, (_, pl_) in enumerate(self.line_keys) if product_line in (None, pl_)]
        if not idx:
            return np.zeros(self.line_paths.shape[1:])
        return self.line_paths[idx].sum(axis=0)

    def params_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            [
                {
                    "facility_id": p.facility_id,
                    "product_line": p.product_line,
                    "n_batches": p.n_batches,
                    "n_failed": p.n_failed,
                    "p_fail_mean": p.p_fail_mean,
                    "p_fail_p05": float(beta_dist.ppf(0.05, p.alpha, p.beta)),
                    "p_fail_p95": float(beta_dist.ppf(0.95, p.alpha, p.beta)),
                    "ratio_mean": float(p.ratios.mean()) if p.ratios.size else None,
                    "ratio_p10": float(np.quantile(p.ratios, 0.1)) if p.ratios.size else None,
                    "ratio_p90": float(np.quantile(p.ratios, 0.9)) if p.ratios.size else None,
                }
                for p in self.params
            ]
        )


def infer_alignment(
    batches: pl.DataFrame, plan: pl.DataFrame, first: date, last: date
) -> tuple[Alignment, dict[str, float]]:
    """Which batch date does `capacity_plan.week_start` count: start or end (output)?

    Score = mean absolute difference between planned batches and realized batch counts per
    history week, keyed by start week vs end week. Lower is better; ties → "end".
    """
    p = (
        plan.filter(pl.col("week_start").is_between(first, last))
        .group_by("week_start", "facility_id", "product_line")
        .agg(pl.col("planned_batches").sum())
    )
    scores = {}
    for key, col in (("start", "start_date"), ("end", "end_date")):
        realized = (
            batches.with_columns(pl.col(col).dt.truncate("1w").alias("week_start"))
            .group_by("week_start", "facility_id", "product_line")
            .len("n")
        )
        j = p.join(realized, on=["week_start", "facility_id", "product_line"], how="left")
        diff = j.select((pl.col("planned_batches") - pl.col("n").fill_null(0)).abs().mean())
        scores[key] = float(diff.item() or 0.0)
    return ("start" if scores["start"] < scores["end"] else "end"), scores


def estimate_lines(batches: pl.DataFrame, last: date, cfg: InHouseConfig) -> list[LineParams]:
    since = last - timedelta(weeks=cfg.history_weeks - 1)
    recent = batches.filter(pl.col("end_date") >= since)
    a0, b0 = cfg.failure_prior
    out = []
    for (fac, line), g in recent.group_by(["facility_id", "product_line"], maintain_order=True):
        failed = int((g["outcome"] == "failed").sum())
        ok = g.filter(pl.col("outcome") != "failed")
        r = (ok["actual_yield_kg"] / ok["planned_yield_kg"]).drop_nans().drop_nulls()
        ratios = r.filter(r.is_finite()).to_numpy()
        out.append(
            LineParams(
                str(fac), str(line), g.height, failed, a0 + failed, b0 + g.height - failed, ratios
            )
        )
    return out


def planned_batches(
    plan: pl.DataFrame, horizon: list[date], alignment: Alignment, batch_weeks: int
) -> pl.DataFrame:
    """Planned output per line × horizon week: `facility_id, product_line, week_start,
    planned_batches, planned_yield_per_batch_kg`. With start alignment, output lands
    `batch_weeks` later (so batches started before the horizon can still deliver into it)."""
    shift = timedelta(weeks=batch_weeks if alignment == "start" else 0)
    shifted = plan.with_columns((pl.col("week_start") + shift).alias("week_start"))
    return (
        shifted.filter(pl.col("week_start").is_in(horizon))
        .group_by("facility_id", "product_line", "week_start")
        .agg(
            pl.col("planned_batches").sum(),
            (
                (pl.col("planned_batches") * pl.col("planned_yield_per_batch_kg")).sum()
                / pl.col("planned_batches").sum()
            ).alias("planned_yield_per_batch_kg"),
        )
        .with_columns(pl.col("planned_yield_per_batch_kg").fill_nan(0.0))
        .sort("facility_id", "product_line", "week_start")  # fixed order: RNG draws depend on it
    )


def simulate_inhouse(
    batches: pl.DataFrame,
    plan: pl.DataFrame,
    window: tuple[date, date],
    horizon: list[date],
    *,
    n_paths: int,
    seed: int,
    cfg: InHouseConfig | None = None,
) -> InHouseCapacity:
    cfg = cfg or InHouseConfig()
    first, last = window
    if cfg.plan_alignment in ("start", "end"):
        alignment: Alignment = cfg.plan_alignment  # type: ignore[assignment]
        scores: dict[str, float] = {}
    else:
        alignment, scores = infer_alignment(batches, plan, first, last)
    dur = batches.select(((pl.col("end_date") - pl.col("start_date")).dt.total_days()).median())
    batch_weeks = round((dur.item() or 0) / 7)
    lines = estimate_lines(batches, last, cfg)
    pb = planned_batches(plan, horizon, alignment, batch_weeks)

    H = len(horizon)
    keys = [(p.facility_id, p.product_line) for p in lines]
    # Plan lines without any batch history: use the pooled prior and ratio 1.0 (flagged).
    for fac, line in pb.select("facility_id", "product_line").unique().sort(pl.all()).iter_rows():
        if (fac, line) not in keys:
            a0, b0 = cfg.failure_prior
            lines.append(LineParams(fac, line, 0, 0, a0, b0, np.array([1.0])))
            keys.append((fac, line))
    paths = np.zeros((len(lines), n_paths, H))
    planned = np.zeros((len(lines), H))
    w_idx = {w: i for i, w in enumerate(horizon)}
    for i, lp in enumerate(lines):
        rows = pb.filter(
            (pl.col("facility_id") == lp.facility_id) & (pl.col("product_line") == lp.product_line)
        )
        if rows.is_empty():
            continue
        gen = seeded_rng(seed, "capacity_inhouse", lp.facility_id, lp.product_line)
        p_fail = gen.beta(lp.alpha, lp.beta, size=(n_paths, 1))  # persistent per path
        ratios = lp.ratios if lp.ratios.size else np.array([1.0])
        cols = ("week_start", "planned_batches", "planned_yield_per_batch_kg")
        for wk, nb, y in rows.select(cols).iter_rows():
            h = w_idx[wk]
            nb = int(nb)
            planned[i, h] = nb * y
            if nb == 0:
                continue
            ok = gen.random((n_paths, nb)) >= p_fail
            r = gen.choice(ratios, size=(n_paths, nb), replace=True)
            paths[i, :, h] = (y * r * ok).sum(axis=1)
    return InHouseCapacity(
        horizon=horizon,
        alignment=alignment,
        alignment_scores=scores,
        params=lines,
        line_keys=keys,
        line_paths=paths,
        planned_kg=planned,
        extra={"batch_weeks": batch_weeks},
    )
