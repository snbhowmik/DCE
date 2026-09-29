"""Numerical-determinism helpers (NFR-2).

Polars' parallel `group_by().sum()` may add floats in a scheduling-dependent order, so a weekly
total can differ in its last bit between runs (e.g. 10487.29 vs 10487.289999999999). Nonlinear
fits amplify that into visibly different outputs. Every float aggregation that feeds a model is
therefore rounded to `SUM_DECIMALS`, far below any meaningful precision for ₹ or kg.
"""

SUM_DECIMALS = 6
