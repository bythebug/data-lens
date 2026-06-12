"""
Time-series analysis: resampling, moving averages, and growth rates.
Builds on aggregation.py for SQL-side grouping; numpy does the window math.
"""

from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from analytics.aggregation import Metric, time_series_aggregate
from analytics.query_builder import assert_safe_column


# ─── Resampling ───────────────────────────────────────────────────────────────


def resample_by_period(
    db: Session,
    dataset_id: int,
    date_column: str,
    value_column: str,
    period: str = "day",
    agg: str = "SUM",
) -> list[dict[str, Any]]:
    """
    Aggregate value_column by date_column truncated to period.
    Thin wrapper around time_series_aggregate that exposes a simpler API.

    period: 'day' | 'week' | 'month' | 'quarter' | 'year'
    agg:    'SUM' | 'AVG' | 'COUNT' | 'MIN' | 'MAX'
    """
    metric = Metric(function=agg, column=value_column)
    return time_series_aggregate(
        db=db,
        dataset_id=dataset_id,
        date_column=date_column,
        metric=metric,
        truncate=period,
    )


# ─── Moving average ───────────────────────────────────────────────────────────


def moving_average(
    db: Session,
    dataset_id: int,
    date_column: str,
    value_column: str,
    window: int = 7,
    period: str = "day",
) -> list[dict[str, Any]]:
    """
    Compute a trailing moving average over a resampled time series.

    Uses np.convolve with mode='valid' then left-pads with None so the output
    has the same length as the input. The first (window-1) entries have no
    moving average because there aren't enough preceding periods.

    Returns one dict per period: {period, value, moving_avg}.
    """
    assert_safe_column(value_column)
    ts = resample_by_period(db, dataset_id, date_column, value_column, period)

    if not ts:
        return []

    metric_key = f"{Metric(function='SUM', column=value_column).alias}"
    raw = np.array(
        [float(r.get(metric_key) or 0) for r in ts],
        dtype=np.float64,
    )
    periods = [r["period"] for r in ts]

    kernel = np.ones(window, dtype=np.float64) / window
    valid_ma = np.convolve(raw, kernel, mode="valid")  # length = n - window + 1

    # Pad the front with NaN so indices align with the original series
    padded_ma = np.concatenate([np.full(window - 1, np.nan), valid_ma])

    return [
        {
            "period": periods[i],
            "value": float(raw[i]),
            "moving_avg": None if np.isnan(padded_ma[i]) else round(float(padded_ma[i]), 4),
        }
        for i in range(len(periods))
    ]


# ─── Growth rate ─────────────────────────────────────────────────────────────


def growth_rate(
    db: Session,
    dataset_id: int,
    metric_column: str,
    period_column: str,
    truncate: str = "month",
) -> list[dict[str, Any]]:
    """
    Period-over-period growth rate:  (current − previous) / |previous| × 100

    The first period has no prior value, so growth_rate_pct is None.
    Division by zero (previous = 0) also returns None.

    Returns one dict per period: {period, value, growth_rate_pct}.
    """
    assert_safe_column(metric_column)
    ts = resample_by_period(db, dataset_id, period_column, metric_column, truncate)

    if not ts:
        return []

    metric_key = Metric(function="SUM", column=metric_column).alias
    values = np.array([float(r.get(metric_key) or 0) for r in ts], dtype=np.float64)
    periods = [r["period"] for r in ts]

    # (current - previous) / |previous| * 100  — suppress divide-by-zero warning
    prev = values[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rates = np.where(prev != 0, np.diff(values) / np.abs(prev) * 100, np.nan)

    result: list[dict[str, Any]] = [
        {"period": periods[0], "value": float(values[0]), "growth_rate_pct": None}
    ]
    for i, rate in enumerate(rates):
        result.append({
            "period": periods[i + 1],
            "value": float(values[i + 1]),
            "growth_rate_pct": None if np.isnan(rate) else round(float(rate), 4),
        })
    return result
