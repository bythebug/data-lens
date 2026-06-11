"""
Per-column and whole-dataset statistics for quick data profiling.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .query_builder import assert_safe_column, get_column_types


def column_stats(
    db: Session,
    dataset_id: int,
    column_name: str,
    col_type: str,
) -> dict[str, Any]:
    """
    Compute statistics for a single column.

    All types:  total_count, null_count, non_null_count, distinct_count
    numeric:  + min, max, avg, stddev
    text:     + min_length, max_length, avg_length
    date:     + min_date, max_date
    """
    assert_safe_column(column_name)
    p = {"dataset_id": dataset_id, "col": column_name}

    base = db.execute(
        text("""
            SELECT
                COUNT(*)                      AS total_count,
                COUNT(data->>:col)            AS non_null_count,
                COUNT(*) - COUNT(data->>:col) AS null_count,
                COUNT(DISTINCT data->>:col)   AS distinct_count
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
        """),
        p,
    ).fetchone()

    result: dict[str, Any] = {
        "column": column_name,
        "data_type": col_type,
        "total_count": base.total_count,
        "non_null_count": base.non_null_count,
        "null_count": base.null_count,
        "distinct_count": base.distinct_count,
    }

    if col_type == "numeric":
        row = db.execute(
            text("""
                SELECT
                    MIN((data->>:col)::numeric)    AS min_val,
                    MAX((data->>:col)::numeric)    AS max_val,
                    AVG((data->>:col)::numeric)    AS avg_val,
                    STDDEV((data->>:col)::numeric) AS stddev_val
                FROM dataset_rows
                WHERE dataset_id = :dataset_id
                  AND data->>:col IS NOT NULL
            """),
            p,
        ).fetchone()
        result.update({
            "min": float(row.min_val) if row.min_val is not None else None,
            "max": float(row.max_val) if row.max_val is not None else None,
            "avg": float(row.avg_val) if row.avg_val is not None else None,
            "stddev": float(row.stddev_val) if row.stddev_val is not None else None,
        })

    elif col_type == "text":
        row = db.execute(
            text("""
                SELECT
                    MIN(length(data->>:col)) AS min_length,
                    MAX(length(data->>:col)) AS max_length,
                    AVG(length(data->>:col)) AS avg_length
                FROM dataset_rows
                WHERE dataset_id = :dataset_id
                  AND data->>:col IS NOT NULL
            """),
            p,
        ).fetchone()
        result.update({
            "min_length": row.min_length,
            "max_length": row.max_length,
            "avg_length": float(row.avg_length) if row.avg_length is not None else None,
        })

    elif col_type == "date":
        row = db.execute(
            text("""
                SELECT
                    MIN((data->>:col)::date) AS min_date,
                    MAX((data->>:col)::date) AS max_date
                FROM dataset_rows
                WHERE dataset_id = :dataset_id
                  AND data->>:col IS NOT NULL
            """),
            p,
        ).fetchone()
        result.update({
            "min_date": str(row.min_date) if row.min_date is not None else None,
            "max_date": str(row.max_date) if row.max_date is not None else None,
        })

    return result


def dataset_stats(db: Session, dataset_id: int) -> list[dict[str, Any]]:
    """Compute statistics for every column in the dataset."""
    return [
        column_stats(db, dataset_id, col_name, col_type)
        for col_name, col_type in get_column_types(db, dataset_id).items()
    ]
