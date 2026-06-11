"""
Dynamic SQL filter builder for JSONB-stored dataset rows.
All column names are validated against a safe identifier regex.
All user-supplied values are passed as bound parameters — never interpolated.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# ─── Safety constants ─────────────────────────────────────────────────────────

ALLOWED_OPERATORS = frozenset({
    "=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "ILIKE",
    "IS NULL", "IS NOT NULL",
})

ALLOWED_SORT_DIRS = frozenset({"ASC", "DESC"})

TYPE_CASTS: dict[str, str] = {
    "numeric": "::numeric",
    "date": "::date",
    "text": "",
}

_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def assert_safe_column(name: str) -> None:
    if not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"Unsafe column name: {name!r}")


def assert_valid_operator(op: str) -> None:
    if op.upper() not in ALLOWED_OPERATORS:
        raise ValueError(
            f"Unknown operator: {op!r}. Allowed: {sorted(ALLOWED_OPERATORS)}"
        )


def col_expr(column: str, col_type: str) -> str:
    """SQL expression that extracts and casts a JSONB key to its declared type."""
    cast = TYPE_CASTS.get(col_type, "")
    return f"(data->>'{column}'){cast}"


# ─── Models ──────────────────────────────────────────────────────────────────


class LogicOp(str, Enum):
    AND = "AND"
    OR = "OR"


@dataclass
class Filter:
    column: str
    operator: str       # =, !=, >, <, >=, <=, IN, LIKE, ILIKE, IS NULL, IS NOT NULL
    value: Any = None   # None for IS NULL / IS NOT NULL


@dataclass
class FilterGroup:
    filters: list[Filter] = field(default_factory=list)
    logic: LogicOp = LogicOp.AND


@dataclass
class QueryResult:
    total: int
    page: int
    page_size: int
    rows: list[dict[str, Any]]


# ─── Column type resolution ───────────────────────────────────────────────────


def get_column_types(db: Session, dataset_id: int) -> dict[str, str]:
    from db.models import DatasetColumn

    rows = (
        db.query(DatasetColumn.column_name, DatasetColumn.data_type)
        .filter(DatasetColumn.dataset_id == dataset_id)
        .all()
    )
    return {r.column_name: r.data_type for r in rows}


# ─── Input parsing ────────────────────────────────────────────────────────────


def parse_filter_input(raw: str | list | dict) -> FilterGroup:
    """
    Parse a filter payload into a FilterGroup.

    Accepts:
      - JSON string: '[{"column":"age","operator":">","value":30}]'
      - List:        [{"column":"age","operator":">","value":30}]
      - Dict with logic key:
          {"logic": "OR", "filters": [{"column":"age","operator":">","value":30}]}
    """
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw

    logic = LogicOp.AND
    filters_data: list = []

    if isinstance(data, dict):
        logic = LogicOp(data.get("logic", "AND").upper())
        filters_data = data.get("filters", [])
    elif isinstance(data, list):
        filters_data = data
    else:
        raise ValueError(f"Unexpected filter input type: {type(data)}")

    return FilterGroup(
        filters=[
            Filter(
                column=f["column"],
                operator=f["operator"],
                value=f.get("value"),
            )
            for f in filters_data
        ],
        logic=logic,
    )


# ─── WHERE clause builder ─────────────────────────────────────────────────────


def build_where_clause(
    filter_group: FilterGroup,
    column_types: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    """
    Build a parameterized WHERE clause from a FilterGroup.
    Returns (sql_fragment, params) where sql_fragment uses :p_N placeholders.

    Examples:
      Filter("age", ">", 30)          → "(data->>'age')::numeric > :p_0"
      Filter("city", "=", "NYC")      → "(data->>'city') = :p_0"
      Filter("name", "ILIKE", "ali%") → "(data->>'name') ILIKE :p_0"
      Filter("tags", "IN", ["a","b"]) → "(data->>'tags') IN (:p_0, :p_1)"
      Filter("bio", "IS NULL")        → "data->>'bio' IS NULL"
    """
    if not filter_group.filters:
        return "TRUE", {}

    parts: list[str] = []
    params: dict[str, Any] = {}
    idx = 0

    for f in filter_group.filters:
        assert_safe_column(f.column)
        op = f.operator.upper()
        assert_valid_operator(op)

        col_type = column_types.get(f.column, "text")
        expr = col_expr(f.column, col_type)

        if op in ("IS NULL", "IS NOT NULL"):
            parts.append(f"data->>'{f.column}' {op}")

        elif op == "IN":
            values = f.value if isinstance(f.value, list) else list(f.value)
            if not values:
                parts.append("FALSE")
                continue
            placeholders = []
            for v in values:
                key = f"p_{idx}"
                params[key] = v
                placeholders.append(f":{key}")
                idx += 1
            parts.append(f"{expr} IN ({', '.join(placeholders)})")

        elif op in ("LIKE", "ILIKE"):
            # LIKE/ILIKE operate on text — skip numeric/date cast
            key = f"p_{idx}"
            params[key] = f.value
            idx += 1
            parts.append(f"(data->>'{f.column}') {op} :{key}")

        else:
            key = f"p_{idx}"
            params[key] = f.value
            idx += 1
            parts.append(f"{expr} {op} :{key}")

    joiner = f" {filter_group.logic.value} "
    return joiner.join(parts), params


# ─── Query execution ──────────────────────────────────────────────────────────


def execute_query(
    db: Session,
    dataset_id: int,
    filter_group: FilterGroup | None = None,
    sort_by: str | None = None,
    sort_dir: str = "ASC",
    page: int = 1,
    page_size: int = 20,
    column_types: dict[str, str] | None = None,
) -> QueryResult:
    """
    Execute a filtered, sorted, and paginated SELECT against dataset_rows.
    column_types is fetched from the DB when not provided.
    """
    if column_types is None:
        column_types = get_column_types(db, dataset_id)

    if filter_group and filter_group.filters:
        where_sql, params = build_where_clause(filter_group, column_types)
        where_clause = f"dataset_id = :dataset_id AND ({where_sql})"
    else:
        where_clause = "dataset_id = :dataset_id"
        params = {}

    params["dataset_id"] = dataset_id

    order_clause = "id ASC"
    if sort_by:
        assert_safe_column(sort_by)
        direction = sort_dir.upper()
        if direction not in ALLOWED_SORT_DIRS:
            direction = "ASC"
        order_clause = f"{col_expr(sort_by, column_types.get(sort_by, 'text'))} {direction} NULLS LAST"

    offset = (page - 1) * page_size

    total: int = db.execute(
        text(f"SELECT COUNT(*) FROM dataset_rows WHERE {where_clause}"),
        params,
    ).scalar() or 0

    if total == 0 or offset >= total:
        return QueryResult(total=int(total), page=page, page_size=page_size, rows=[])

    rows_raw = db.execute(
        text(f"""
            SELECT id, data
            FROM dataset_rows
            WHERE {where_clause}
            ORDER BY {order_clause}
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
    ).fetchall()

    return QueryResult(
        total=int(total),
        page=page,
        page_size=page_size,
        rows=[{"id": row.id, "data": row.data} for row in rows_raw],
    )
