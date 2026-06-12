"""
Automatic index suggestion, creation, and maintenance for data-lens datasets.

Index philosophy:
- One partial expression index per (dataset, column) pair, scoped with
  WHERE dataset_id = N to keep each index small and dataset-isolated.
- btree for numeric / date (supports range + ORDER BY).
- hash for high-cardinality text (equality-only, smaller than btree).
- Skip low-cardinality text (< 5% distinct ratio) — full scan is cheaper.
- Skip columns that already have a GIN/FTS index registered.
- Composite indexes for common (date + metric) aggregation patterns.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.query_builder import assert_safe_column
from db.models import DatasetColumn, SearchIndex

logger = logging.getLogger(__name__)

# Columns with fewer distinct values than this fraction are low-cardinality
_LOW_CARDINALITY_THRESHOLD = 0.05


@dataclass
class IndexRecommendation:
    dataset_id: int
    column_name: str
    index_type: str                        # btree | hash | gin
    reason: str
    partial_condition: str = ""            # e.g. "status = 'active'"
    composite_with: list[str] = field(default_factory=list)


def _distinct_ratio(db: Session, dataset_id: int, column_name: str) -> float:
    """Fraction of rows that have a distinct value for this column (0–1)."""
    row = db.execute(
        text("""
            SELECT
                COUNT(DISTINCT data->>:col)::float
                    / NULLIF(COUNT(*), 0)
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
        """),
        {"dataset_id": dataset_id, "col": column_name},
    ).scalar()
    return float(row or 0.0)


def suggest_indexes(db: Session, dataset_id: int) -> list[IndexRecommendation]:
    """
    Analyse columns and return index recommendations.

    Rules applied (in order):
    1. Skip columns that already have a fulltext (GIN) index registered.
    2. Numeric → btree partial expression index.
    3. Date    → btree partial expression index.
    4. Text, high cardinality (>= 5% distinct) → hash for equality lookups.
    5. Text, low cardinality (< 5% distinct)   → skip.
    6. Composite: (date, numeric) pair → btree for time-series aggregation.
    7. Composite: two numeric columns → btree for multi-column filters.
    """
    cols = (
        db.query(DatasetColumn)
        .filter(DatasetColumn.dataset_id == dataset_id)
        .all()
    )

    existing_fts = {
        si.column_name
        for si in db.query(SearchIndex)
        .filter(
            SearchIndex.dataset_id == dataset_id,
            SearchIndex.index_type == "fulltext",
        )
        .all()
    }

    recommendations: list[IndexRecommendation] = []
    numeric_cols: list[str] = []
    date_cols: list[str] = []

    for col in cols:
        name, dtype = col.column_name, col.data_type

        if name in existing_fts:
            continue

        if dtype == "numeric":
            numeric_cols.append(name)
            recommendations.append(IndexRecommendation(
                dataset_id=dataset_id,
                column_name=name,
                index_type="btree",
                reason="Numeric column — btree supports range queries and ORDER BY",
            ))

        elif dtype == "date":
            date_cols.append(name)
            recommendations.append(IndexRecommendation(
                dataset_id=dataset_id,
                column_name=name,
                index_type="btree",
                reason="Date column — btree supports range queries and time-series ORDER BY",
            ))

        elif dtype == "text":
            ratio = _distinct_ratio(db, dataset_id, name)
            if ratio >= _LOW_CARDINALITY_THRESHOLD:
                recommendations.append(IndexRecommendation(
                    dataset_id=dataset_id,
                    column_name=name,
                    index_type="hash",
                    reason=f"High-cardinality text ({ratio:.0%} distinct) — hash for equality",
                ))
            else:
                logger.debug(
                    "Skipping index on '%s' (low cardinality: %.1f%%)", name, ratio * 100
                )

    # Composite: date + first numeric → time-series aggregation
    if date_cols and numeric_cols:
        recommendations.append(IndexRecommendation(
            dataset_id=dataset_id,
            column_name=date_cols[0],
            index_type="btree",
            reason="Composite — date + metric for time-series GROUP BY + ORDER BY",
            composite_with=[numeric_cols[0]],
        ))

    # Composite: first two numeric columns → multi-column filter coverage
    if len(numeric_cols) >= 2:
        recommendations.append(IndexRecommendation(
            dataset_id=dataset_id,
            column_name=numeric_cols[0],
            index_type="btree",
            reason="Composite — two numeric columns for multi-column range filters",
            composite_with=[numeric_cols[1]],
        ))

    return recommendations


def create_expression_index(
    db: Session,
    dataset_id: int,
    column_name: str,
    col_type: str,
    index_type: str = "btree",
) -> str:
    """
    CREATE a partial expression index on a single JSONB column.
    The WHERE dataset_id = N clause keeps the index small and dataset-isolated.
    Returns the index name.
    """
    assert_safe_column(column_name)
    if index_type not in ("btree", "hash", "gin"):
        raise ValueError(f"Unsupported index type: {index_type!r}")

    cast = {"numeric": "::numeric", "date": "::date", "text": ""}.get(col_type, "")
    expr = f"(data->>'{column_name}'){cast}"
    idx_name = f"idx_{index_type}_{dataset_id}_{column_name}"

    db.execute(text(f"""
        CREATE INDEX IF NOT EXISTS {idx_name}
        ON dataset_rows USING {index_type} (({expr}))
        WHERE dataset_id = {int(dataset_id)}
    """))
    db.commit()
    logger.info("Created index %s", idx_name)
    return idx_name


def drop_unused_indexes(
    db: Session,
    dataset_id: int,
    min_scans: int = 0,
) -> list[str]:
    """
    Drop expression indexes for dataset_id that have been used ≤ min_scans times.
    Queries pg_stat_user_indexes which tracks cumulative index scans since last
    pg_stat_reset. Always verify before dropping in production.
    Returns names of dropped indexes.
    """
    rows = db.execute(
        text("""
            SELECT indexrelname
            FROM pg_stat_user_indexes
            WHERE indexrelname LIKE :pattern
              AND idx_scan <= :min_scans
        """),
        {"pattern": f"idx_%_{dataset_id}_%", "min_scans": min_scans},
    ).fetchall()

    dropped: list[str] = []
    for row in rows:
        idx_name = row[0]
        db.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
        dropped.append(idx_name)
        logger.info("Dropped unused index %s (scans=%d)", idx_name, min_scans)

    if dropped:
        db.commit()
    return dropped


def get_index_stats(db: Session, dataset_id: int) -> list[dict]:
    """
    Return size and usage stats for all indexes belonging to this dataset,
    sourced from pg_stat_user_indexes and pg_relation_size.
    Useful for monitoring index bloat.
    """
    rows = db.execute(
        text("""
            SELECT
                indexrelname                                          AS index_name,
                idx_scan                                              AS scan_count,
                idx_tup_read                                          AS tuples_read,
                idx_tup_fetch                                         AS tuples_fetched,
                pg_size_pretty(pg_relation_size(indexrelid))          AS index_size,
                pg_relation_size(indexrelid)                          AS index_size_bytes
            FROM pg_stat_user_indexes
            WHERE schemaname = 'public'
              AND indexrelname LIKE :pattern
            ORDER BY index_size_bytes DESC
        """),
        {"pattern": f"idx_%_{dataset_id}_%"},
    ).fetchall()

    return [
        {
            "index_name": r.index_name,
            "scan_count": r.scan_count,
            "tuples_read": r.tuples_read,
            "tuples_fetched": r.tuples_fetched,
            "index_size": r.index_size,
            "index_size_bytes": r.index_size_bytes,
        }
        for r in rows
    ]
