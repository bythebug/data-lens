"""
Aggregation engine: GROUP BY, aggregate metrics, and time-series grouping
over JSONB-stored dataset rows.
"""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .query_builder import (
    FilterGroup,
    assert_safe_column,
    build_where_clause,
    col_expr,
    get_column_types,
)

ALLOWED_AGG_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX", "STDDEV"})
ALLOWED_TIME_TRUNCATIONS = frozenset({"day", "week", "month", "quarter", "year"})

_METRIC_RE = re.compile(
    r"^(COUNT|SUM|AVG|MIN|MAX|STDDEV)\(([a-zA-Z_*][a-zA-Z0-9_]*)\)$",
    re.IGNORECASE,
)


@dataclass
class Metric:
    function: str   # COUNT, SUM, AVG, MIN, MAX, STDDEV
    column: str     # column name or "*" for COUNT(*)
    alias: str = ""

    def __post_init__(self) -> None:
        self.function = self.function.upper()
        if not self.alias:
            self.alias = f"{self.function.lower()}_{self.column.replace('*', 'all')}"


def parse_metric(metric_str: str) -> Metric:
    """
    Parse "SUM(revenue)"  → Metric(function="SUM",   column="revenue", alias="sum_revenue")
    Parse "COUNT(*)"      → Metric(function="COUNT",  column="*",       alias="count_all")
    Parse "STDDEV(score)" → Metric(function="STDDEV", column="score",   alias="stddev_score")
    """
    m = _METRIC_RE.match(metric_str.strip())
    if not m:
        raise ValueError(
            f"Invalid metric {metric_str!r}. Expected FUNCTION(column), e.g. SUM(revenue)"
        )
    func, column = m.group(1).upper(), m.group(2)
    if func not in ALLOWED_AGG_FUNCTIONS:
        raise ValueError(f"Unsupported function: {func}. Allowed: {sorted(ALLOWED_AGG_FUNCTIONS)}")
    if column != "*":
        assert_safe_column(column)
    return Metric(function=func, column=column)


def _metric_sql(metric: Metric, col_type: str) -> str:
    """SQL expression for a single aggregate metric over a JSONB column."""
    if metric.column == "*":
        return f"COUNT(*) AS {metric.alias}"
    if metric.function in ("SUM", "AVG", "STDDEV") or col_type == "numeric":
        inner = f"(data->>'{metric.column}')::numeric"
    elif col_type == "date":
        inner = f"(data->>'{metric.column}')::date"
    else:
        inner = f"data->>'{metric.column}'"
    return f"{metric.function}({inner}) AS {metric.alias}"


def aggregate_dataset(
    db: Session,
    dataset_id: int,
    group_by: list[str],
    metrics: list[Metric],
    filter_group: FilterGroup | None = None,
    sort_by: str | None = None,
    sort_dir: str = "DESC",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    GROUP BY one or more columns and compute aggregate metrics.

    Example:
      group_by=["region"], metrics=[Metric("SUM","revenue"), Metric("AVG","price")]
      →  SELECT data->>'region', SUM(...), AVG(...) FROM ... GROUP BY data->>'region'

    sort_by: a metric alias ("sum_revenue") or a group-by column name.
    """
    if not metrics:
        raise ValueError("At least one metric is required")
    if not group_by:
        raise ValueError("At least one group_by column is required")

    for col in group_by:
        assert_safe_column(col)

    column_types = get_column_types(db, dataset_id)

    if filter_group and filter_group.filters:
        where_sql, params = build_where_clause(filter_group, column_types)
        where_clause = f"dataset_id = :dataset_id AND ({where_sql})"
    else:
        where_clause = "dataset_id = :dataset_id"
        params = {}
    params["dataset_id"] = dataset_id

    select_groups = [f"data->>'{c}' AS {c}" for c in group_by]
    select_metrics = [_metric_sql(m, column_types.get(m.column, "text")) for m in metrics]
    group_exprs = [f"data->>'{c}'" for c in group_by]

    order_col = sort_by or metrics[0].alias
    direction = sort_dir.upper() if sort_dir.upper() in ("ASC", "DESC") else "DESC"

    rows_raw = db.execute(
        text(f"""
            SELECT {', '.join(select_groups + select_metrics)}
            FROM dataset_rows
            WHERE {where_clause}
            GROUP BY {', '.join(group_exprs)}
            ORDER BY {order_col} {direction} NULLS LAST
            LIMIT :limit
        """),
        {**params, "limit": limit},
    ).fetchall()

    return [dict(row._mapping) for row in rows_raw]


def time_series_aggregate(
    db: Session,
    dataset_id: int,
    date_column: str,
    metric: Metric,
    truncate: str = "month",
    filter_group: FilterGroup | None = None,
) -> list[dict[str, Any]]:
    """
    Aggregate a metric over time, grouped by a date truncation period.

    truncate: 'day' | 'week' | 'month' | 'quarter' | 'year'

    Returns rows like: [{"period": "2024-01-01", "sum_revenue": 12345.0}, ...]
    """
    assert_safe_column(date_column)
    truncate = truncate.lower()
    if truncate not in ALLOWED_TIME_TRUNCATIONS:
        raise ValueError(
            f"Invalid truncation {truncate!r}. Allowed: {sorted(ALLOWED_TIME_TRUNCATIONS)}"
        )

    column_types = get_column_types(db, dataset_id)

    if filter_group and filter_group.filters:
        where_sql, params = build_where_clause(filter_group, column_types)
        where_clause = f"dataset_id = :dataset_id AND ({where_sql})"
    else:
        where_clause = "dataset_id = :dataset_id"
        params = {}
    params["dataset_id"] = dataset_id
    params["truncate"] = truncate

    metric_expr = _metric_sql(metric, column_types.get(metric.column, "text"))

    rows_raw = db.execute(
        text(f"""
            SELECT
                date_trunc(:truncate, (data->>'{date_column}')::date) AS period,
                {metric_expr}
            FROM dataset_rows
            WHERE {where_clause}
              AND data->>'{date_column}' IS NOT NULL
            GROUP BY period
            ORDER BY period ASC
        """),
        params,
    ).fetchall()

    return [
        {"period": str(row.period), metric.alias: row._mapping[metric.alias]}
        for row in rows_raw
    ]
