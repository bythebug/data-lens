"""
Statistical analysis using numpy and scipy.

Hybrid approach: PostgreSQL pre-filters and limits data size, then numpy/scipy
does the actual computation. MAX_SAMPLE caps the rows fetched so memory stays
bounded even on million-row datasets.
"""

from typing import Any

import numpy as np
from scipy import stats as scipy_stats
from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.query_builder import assert_safe_column, get_column_types

MAX_SAMPLE = 50_000  # maximum rows fetched per column for numpy computation


# ─── Data fetching ────────────────────────────────────────────────────────────


def _fetch_values(
    db: Session,
    dataset_id: int,
    column: str,
    limit: int = MAX_SAMPLE,
) -> np.ndarray:
    """
    Fetch non-null numeric values for one column into a float64 array.
    Uses LIMIT without ORDER BY for speed — order doesn't matter for statistics.
    """
    assert_safe_column(column)
    rows = db.execute(
        text("""
            SELECT (data->>:col)::numeric
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
              AND data->>:col IS NOT NULL
            LIMIT :limit
        """),
        {"dataset_id": dataset_id, "col": column, "limit": limit},
    ).fetchall()
    return np.array([float(r[0]) for r in rows], dtype=np.float64)


# ─── Basic statistics ─────────────────────────────────────────────────────────


def basic_stats(
    db: Session,
    dataset_id: int,
    column: str,
) -> dict[str, Any] | None:
    """
    Descriptive statistics for a numeric column.

    Returns count, mean, median, std (sample), variance, min, max,
    skewness, and excess kurtosis.
    Returns None if the column has no non-null values.
    """
    values = _fetch_values(db, dataset_id, column)
    if values.size == 0:
        return None

    return {
        "column": column,
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values, ddof=1)),       # sample std dev (ddof=1)
        "variance": float(np.var(values, ddof=1)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "skewness": float(scipy_stats.skew(values)),
        "kurtosis": float(scipy_stats.kurtosis(values)),  # excess kurtosis
    }


# ─── Quantiles ────────────────────────────────────────────────────────────────


def quantiles(
    db: Session,
    dataset_id: int,
    column: str,
) -> dict[str, float] | None:
    """
    Compute percentiles p25, p50, p75, p90, p99 using numpy linear interpolation.
    Returns None if the column has no non-null values.
    """
    values = _fetch_values(db, dataset_id, column)
    if values.size == 0:
        return None

    p25, p50, p75, p90, p99 = np.percentile(values, [25, 50, 75, 90, 99])
    return {
        "column": column,
        "p25": float(p25),
        "p50": float(p50),
        "p75": float(p75),
        "p90": float(p90),
        "p99": float(p99),
    }


# ─── Correlation matrix ───────────────────────────────────────────────────────


def correlation_matrix(
    db: Session,
    dataset_id: int,
) -> dict[str, Any]:
    """
    Pearson correlation matrix for all numeric columns in the dataset.

    Fetches all numeric columns in a single query (rows where ALL columns
    are non-null) and computes np.corrcoef. Requires at least 2 numeric columns.
    """
    col_types = get_column_types(db, dataset_id)
    numeric_cols = [c for c, t in col_types.items() if t == "numeric"]

    if len(numeric_cols) < 2:
        return {"columns": numeric_cols, "matrix": []}

    for col in numeric_cols:
        assert_safe_column(col)

    select_expr = ", ".join(
        f"(data->>'{c}')::numeric AS {c}" for c in numeric_cols
    )
    not_null = " AND ".join(f"data->>'{c}' IS NOT NULL" for c in numeric_cols)

    rows = db.execute(
        text(f"""
            SELECT {select_expr}
            FROM dataset_rows
            WHERE dataset_id = :dataset_id AND {not_null}
            LIMIT :limit
        """),
        {"dataset_id": dataset_id, "limit": MAX_SAMPLE},
    ).fetchall()

    if len(rows) < 2:
        return {"columns": numeric_cols, "matrix": []}

    matrix = np.array(
        [[float(getattr(r, c)) for c in numeric_cols] for r in rows],
        dtype=np.float64,
    )

    corr = np.corrcoef(matrix, rowvar=False)

    return {
        "columns": numeric_cols,
        "matrix": [
            {
                "column": numeric_cols[i],
                "correlations": {
                    numeric_cols[j]: round(float(corr[i, j]), 4)
                    for j in range(len(numeric_cols))
                },
            }
            for i in range(len(numeric_cols))
        ],
    }


# ─── Distribution / histogram ─────────────────────────────────────────────────


def distribution_analysis(
    db: Session,
    dataset_id: int,
    column: str,
    buckets: int = 20,
) -> dict[str, Any] | None:
    """
    Histogram of value distribution using np.histogram.
    Returns bin edges, bin centres, and per-bucket counts.
    """
    values = _fetch_values(db, dataset_id, column)
    if values.size == 0:
        return None

    buckets = max(2, min(buckets, 100))
    counts, bin_edges = np.histogram(values, bins=buckets)
    bin_centres = (bin_edges[:-1] + bin_edges[1:]) / 2

    return {
        "column": column,
        "buckets": buckets,
        "total_values": int(values.size),
        "counts": counts.tolist(),
        "bin_edges": [round(float(e), 6) for e in bin_edges],
        "bin_centres": [round(float(c), 6) for c in bin_centres],
    }


# ─── Outlier detection (IQR method) ──────────────────────────────────────────


def outlier_detection(
    db: Session,
    dataset_id: int,
    column: str,
) -> dict[str, Any] | None:
    """
    Identify outliers using the Tukey IQR fence method:
      lower_fence = Q1 - 1.5 × IQR
      upper_fence = Q3 + 1.5 × IQR

    Steps:
    1. Compute Q1, Q3, IQR from the numpy sample.
    2. Query PostgreSQL for rows outside the fences (with row IDs).

    Returns fence values, outlier count, and up to 200 outlier rows.
    """
    values = _fetch_values(db, dataset_id, column)
    if values.size == 0:
        return None

    assert_safe_column(column)
    q1, q3 = float(np.percentile(values, 25)), float(np.percentile(values, 75))
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    outlier_rows = db.execute(
        text(f"""
            SELECT id, data
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
              AND data->>'{column}' IS NOT NULL
              AND (
                    (data->>'{column}')::numeric < :lower
                 OR (data->>'{column}')::numeric > :upper
              )
            ORDER BY (data->>'{column}')::numeric
            LIMIT 200
        """),
        {
            "dataset_id": dataset_id,
            "lower": lower_fence,
            "upper": upper_fence,
        },
    ).fetchall()

    return {
        "column": column,
        "method": "IQR (Tukey fences)",
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "iqr": round(iqr, 4),
        "lower_fence": round(lower_fence, 4),
        "upper_fence": round(upper_fence, 4),
        "sample_size": int(values.size),
        "outlier_count": len(outlier_rows),
        "outliers": [
            {"id": r.id, "value": r.data.get(column)}
            for r in outlier_rows
        ],
    }
