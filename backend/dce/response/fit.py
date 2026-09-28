"""Spend → D2C demand response per region: adstock + Hill (ARCH §5.6, T4.1).

    adstock_t = spend_t + θ·adstock_{t−1}
    lift_t    = β · A^α / (κ^α + A^α)
    demand_t  = organic_t + lift_t + ε_t,  organic = level + trend + Fourier(52, K)

Fitting is variable projection: bounded search over (θ, α, log κ) with the linear terms
(organic coefficients, β ≥ 0) solved exactly inside. Parameter uncertainty from a moving-block
residual bootstrap. Guards: extrapolation cap, and `low_confidence` when spend barely varies,
elasticity is implausible, or the bootstrap is unstable (ARCH §5.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize, nnls

from dce.seeds import rng as seeded_rng


@dataclass(frozen=True)
class ResponseConfig:
    theta_bounds: tuple[float, float] = (0.0, 0.9)
    alpha_bounds: tuple[float, float] = (0.5, 3.0)
    kappa_rel_bounds: tuple[float, float] = (0.1, 10.0)
    fourier_k: int = 2
    n_starts: int = 4
    n_boot: int = 40
    boot_block_weeks: int = 8
    elasticity_bounds: tuple[float, float] = (0.0, 0.8)
    max_rel_ci_width: float = 1.5
    min_spend_cv: float = 0.10
    extrapolation_factor: float = 1.5

    @classmethod
    def from_app(cls, app: dict[str, Any]) -> ResponseConfig:
        r = app.get("response", {})
        d = cls()

        def pair(key: str) -> tuple[float, float]:
            v = r.get(key, getattr(d, key))
            return (float(v[0]), float(v[1]))

        return cls(
            theta_bounds=pair("theta_bounds"),
            alpha_bounds=pair("alpha_bounds"),
            kappa_rel_bounds=pair("kappa_rel_bounds"),
            fourier_k=int(r.get("fourier_k", d.fourier_k)),
            n_starts=int(r.get("n_starts", d.n_starts)),
            n_boot=int(r.get("n_boot", d.n_boot)),
            boot_block_weeks=int(r.get("boot_block_weeks", d.boot_block_weeks)),
            elasticity_bounds=pair("elasticity_bounds"),
            max_rel_ci_width=float(r.get("max_rel_ci_width", d.max_rel_ci_width)),
            min_spend_cv=float(r.get("min_spend_cv", d.min_spend_cv)),
            extrapolation_factor=float(r.get("extrapolation_factor", d.extrapolation_factor)),
        )


def adstock(spend: np.ndarray, theta: float) -> np.ndarray:
    out = np.empty_like(spend, dtype=float)
    acc = 0.0
    for t, s in enumerate(spend):
        acc = s + theta * acc
        out[t] = acc
    return out


def hill(a: np.ndarray, alpha: float, kappa: float) -> np.ndarray:
    a = np.maximum(a, 0.0)
    num = a**alpha
    return num / (kappa**alpha + num)


def organic_design(n: int, k: int, week_of_year: np.ndarray | None = None) -> np.ndarray:
    """[n, 2 + 2k]: intercept, trend (in years), and k Fourier pairs of the 52-week season."""
    t = np.arange(n, dtype=float)
    woy = week_of_year if week_of_year is not None else t
    cols = [np.ones(n), t / 52.0]
    for j in range(1, k + 1):
        cols += [np.sin(2 * np.pi * j * woy / 52.18), np.cos(2 * np.pi * j * woy / 52.18)]
    return np.column_stack(cols)


@dataclass
class _Solve:
    theta: float
    alpha: float
    kappa: float
    beta: float
    coef: np.ndarray
    sse: float
    fitted: np.ndarray


def _inner(
    y: np.ndarray, X0: np.ndarray, spend: np.ndarray, theta: float, alpha: float, kappa: float
) -> _Solve:
    h = hill(adstock(spend, theta), alpha, kappa)
    X = np.column_stack([X0, h])
    # Organic coefficients unconstrained, β ≥ 0: NNLS on [X0, −X0, h] is equivalent.
    Xn = np.column_stack([X0, -X0, h])
    w, _ = nnls(Xn, y, maxiter=10 * Xn.shape[1])
    p = X0.shape[1]
    coef = np.concatenate([w[:p] - w[p : 2 * p], w[2 * p :]])
    fitted = X @ coef
    sse = float(((y - fitted) ** 2).sum())
    return _Solve(theta, alpha, kappa, float(coef[-1]), coef[:-1], sse, fitted)


def fit_once(
    y: np.ndarray, spend: np.ndarray, X0: np.ndarray, cfg: ResponseConfig, gen: np.random.Generator
) -> _Solve:
    a_mean = max(float(adstock(spend, float(np.mean(cfg.theta_bounds))).mean()), 1e-9)
    lk = (np.log(cfg.kappa_rel_bounds[0] * a_mean), np.log(cfg.kappa_rel_bounds[1] * a_mean))
    bounds = [cfg.theta_bounds, cfg.alpha_bounds, lk]

    def obj(x: np.ndarray) -> float:
        return _inner(y, X0, spend, x[0], x[1], float(np.exp(x[2]))).sse

    starts = [np.array([np.mean(cfg.theta_bounds), 1.0, np.log(a_mean)])]
    for _ in range(cfg.n_starts - 1):
        starts.append(np.array([gen.uniform(*b) for b in bounds]))
    best = None
    for x0 in starts:
        res = minimize(obj, x0, method="L-BFGS-B", bounds=bounds)
        if best is None or res.fun < best.fun:
            best = res
    assert best is not None
    th, al, lk_ = best.x
    return _inner(y, X0, spend, float(th), float(al), float(np.exp(lk_)))


@dataclass
class ResponseFit:
    region_id: str
    n_weeks: int
    theta: float
    alpha: float
    kappa: float
    beta: float
    organic_coef: np.ndarray
    r2: float
    mean_spend: float
    max_spend: float
    spend_cap: float
    elasticity: float | None
    lift_at_mean: float
    ci: dict[str, tuple[float, float]]  # P5–P95 per parameter and for lift_at_mean
    low_confidence: bool
    reasons: list[str] = field(default_factory=list)

    def steady_lift(self, weekly_spend: np.ndarray | float) -> np.ndarray:
        """Weekly lift at a constant weekly spend s (steady-state adstock s/(1−θ))."""
        s = np.asarray(weekly_spend, dtype=float)
        return self.beta * hill(s / (1 - self.theta), self.alpha, self.kappa)

    def lag_weights(self, n: int) -> np.ndarray:
        """Share of one week's spend effect landing k weeks later (geometric, normalized)."""
        w = self.theta ** np.arange(n)
        return w / w.sum()

    def summary(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id, "n_weeks": self.n_weeks, "theta": self.theta,
            "alpha": self.alpha, "kappa": self.kappa, "beta": self.beta, "r2": self.r2,
            "mean_spend": self.mean_spend, "max_spend": self.max_spend,
            "spend_cap": self.spend_cap, "elasticity": self.elasticity,
            "lift_at_mean": self.lift_at_mean, "low_confidence": self.low_confidence,
            "reasons": self.reasons,
            **{f"{k}_p05": v[0] for k, v in self.ci.items()},
            **{f"{k}_p95": v[1] for k, v in self.ci.items()},
        }  # fmt: skip


def _block_resample(resid: np.ndarray, block: int, gen: np.random.Generator) -> np.ndarray:
    n = len(resid)
    out = np.empty(n)
    i = 0
    while i < n:
        start = int(gen.integers(0, max(1, n - block + 1)))
        take = min(block, n - i)
        out[i : i + take] = resid[start : start + take]
        i += take
    return out


def fit_region(
    region_id: str,
    demand: np.ndarray,
    spend: np.ndarray,
    cfg: ResponseConfig,
    seed: int,
    week_of_year: np.ndarray | None = None,
) -> ResponseFit:
    y = np.asarray(demand, dtype=float)
    s = np.asarray(spend, dtype=float)
    n = len(y)
    X0 = organic_design(n, cfg.fourier_k, week_of_year)
    gen = seeded_rng(seed, "response_fit", region_id)
    best = fit_once(y, s, X0, cfg, gen)
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    r2 = 1 - best.sse / ss_tot

    mean_s, max_s = float(s.mean()), float(s.max())
    a_mean = mean_s / (1 - best.theta)
    lift_mean = float(best.beta * hill(np.array([a_mean]), best.alpha, best.kappa)[0])
    d_mean = float(y.mean())
    # d lift / d A · A = lift · α κ^α / (κ^α + A^α); elasticity of demand at mean spend
    k_a = best.kappa**best.alpha
    elasticity = (
        lift_mean * best.alpha * k_a / (k_a + a_mean**best.alpha) / d_mean
        if d_mean > 0 and a_mean > 0
        else None
    )

    keys = ("theta", "alpha", "kappa", "beta", "lift_at_mean")
    boots: dict[str, list[float]] = {k: [] for k in keys}
    resid = y - best.fitted
    for _ in range(cfg.n_boot):
        y_b = best.fitted + _block_resample(resid, cfg.boot_block_weeks, gen)
        b = fit_once(y_b, s, X0, cfg, gen)
        boots["theta"].append(b.theta)
        boots["alpha"].append(b.alpha)
        boots["kappa"].append(b.kappa)
        boots["beta"].append(b.beta)
        boots["lift_at_mean"].append(
            float(b.beta * hill(np.array([mean_s / (1 - b.theta)]), b.alpha, b.kappa)[0])
        )
    ci = {
        k: (float(np.quantile(v, 0.05)), float(np.quantile(v, 0.95))) for k, v in boots.items() if v
    }

    reasons = []
    cv = float(s.std() / s.mean()) if s.mean() > 0 else 0.0
    if cv < cfg.min_spend_cv:
        reasons.append(f"spend barely varies (CV {cv:.2f} < {cfg.min_spend_cv})")
    lo, hi = cfg.elasticity_bounds
    if elasticity is None or not (lo <= elasticity <= hi):
        reasons.append(f"implausible elasticity {elasticity}")
    if boots["lift_at_mean"]:
        med = float(np.median(boots["lift_at_mean"]))
        width = ci["lift_at_mean"][1] - ci["lift_at_mean"][0]
        if med <= 0 or width / med > cfg.max_rel_ci_width:
            rel = width / med if med > 0 else float("inf")
            reasons.append(f"unstable bootstrap (lift CI width/median {rel:.2f})")
    return ResponseFit(
        region_id=region_id,
        n_weeks=n,
        theta=best.theta,
        alpha=best.alpha,
        kappa=best.kappa,
        beta=best.beta,
        organic_coef=best.coef,
        r2=r2,
        mean_spend=mean_s,
        max_spend=max_s,
        spend_cap=max_s * cfg.extrapolation_factor,
        elasticity=elasticity,
        lift_at_mean=lift_mean,
        ci=ci,
        low_confidence=bool(reasons),
        reasons=reasons,
    )
