"""Planning scope (A-001): the single commercial product line and its production SKUs.

Demand for SKUs outside this scope (pilot / R&D lines) cannot be served from the planned line's
capacity; it is excluded from forecasting and allocation and reported as out-of-scope demand.
"""

from __future__ import annotations

import polars as pl


def production_line(skus: pl.DataFrame) -> str:
    lines = skus.filter(pl.col("status") == "production")["product_line"].unique().sort().to_list()
    if not lines:
        raise ValueError("no SKU in production status")
    if len(lines) > 1:
        raise NotImplementedError(
            f"multiple production lines {lines}: v1 plans one commercial line (A-001)"
        )
    return str(lines[0])


def planning_skus(skus: pl.DataFrame) -> list[str]:
    """Production-status SKUs of the production line, sorted."""
    line = production_line(skus)
    return (
        skus.filter((pl.col("status") == "production") & (pl.col("product_line") == line))["sku_id"]
        .sort()
        .to_list()
    )
