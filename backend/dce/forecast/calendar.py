"""Indian public holiday / festival calendar (public knowledge; allowed per ARCH §5.4)."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays
import polars as pl


@lru_cache(maxsize=8)
def _india(years: tuple[int, ...]) -> dict[date, str]:
    return dict(holidays.India(years=list(years)))


def weekly_holidays(first_week: date, last_week: date) -> pl.DataFrame:
    """`week_start, holiday_count, festival_week` for each Monday in [first_week, last_week].

    `festival_week` marks weeks containing Diwali, Holi, Dussehra, Eid, or Christmas.
    """
    years = tuple(range(first_week.year, last_week.year + 2))
    cal = _india(years)
    majors = ("Diwali", "Holi", "Dussehra", "Id-ul", "Christmas")
    rows = []
    wk = first_week
    while wk <= last_week:
        names = [cal[d] for d in (wk + timedelta(days=i) for i in range(7)) if d in cal]
        rows.append((wk, len(names), any(m in n for n in names for m in majors)))
        wk += timedelta(weeks=1)
    return pl.DataFrame(
        rows,
        schema={"week_start": pl.Date, "holiday_count": pl.Int32, "festival_week": pl.Boolean},
        orient="row",
    )
