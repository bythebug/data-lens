"""
Full-text search engine for data-lens datasets.
Query parsing is done in Python; actual FTS is delegated to PostgreSQL
via websearch_to_tsquery + ts_rank, which handles stemming and stop words
natively with the 'english' text search configuration.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from analytics.optimizer import timed_execute
from db.models import DatasetColumn, SearchIndex
from .fts import (
    FTS_CONFIG,
    build_tsvector_sql,
    create_fts_index,
    get_text_columns,
    index_exists,
)


class QueryType(Enum):
    SIMPLE = "simple"    # plain words → implicit AND between them
    PHRASE = "phrase"    # "quoted phrase" → adjacent-word match
    BOOLEAN = "boolean"  # explicit AND / OR operators


@dataclass
class ParsedQuery:
    original: str
    query_type: QueryType
    terms: list[str]
    normalized: str


@dataclass
class SearchResult:
    total: int
    page: int
    page_size: int
    rows: list[dict[str, Any]]


# ─── Query parsing ────────────────────────────────────────────────────────────

_PHRASE_RE = re.compile(r'"[^"]+"')
_BOOLEAN_RE = re.compile(r"\b(AND|OR)\b")
_TOKEN_RE = re.compile(r'"[^"]*"|\S+')


def parse_query(query_str: str) -> ParsedQuery:
    """
    Classify and normalise a raw user query.

    Supported syntax (passed through to websearch_to_tsquery):
      hello world        → implicit AND
      "hello world"      → phrase match
      hello AND world    → explicit AND
      hello OR world     → boolean OR
      hello -world       → NOT
    """
    s = query_str.strip()
    if not s:
        raise ValueError("Search query cannot be empty")

    has_phrase = bool(_PHRASE_RE.search(s))
    has_boolean = bool(_BOOLEAN_RE.search(s))

    if has_boolean:
        qtype = QueryType.BOOLEAN
    elif has_phrase:
        qtype = QueryType.PHRASE
    else:
        qtype = QueryType.SIMPLE

    terms = [
        t.strip('"')
        for t in _TOKEN_RE.findall(s)
        if t.upper() not in ("AND", "OR")
    ]

    return ParsedQuery(original=s, query_type=qtype, terms=terms, normalized=s)


# ─── Index management ────────────────────────────────────────────────────────


def build_search_index(db: Session, dataset_id: int, column_name: str) -> bool:
    """
    Register a fulltext index for column_name and create it in PostgreSQL.
    Returns True if created, False if it already existed.
    """
    if index_exists(db, dataset_id, column_name):
        return False

    create_fts_index(db, dataset_id, column_name)

    existing = (
        db.query(SearchIndex)
        .filter_by(dataset_id=dataset_id, column_name=column_name, index_type="fulltext")
        .first()
    )
    if not existing:
        db.add(SearchIndex(
            dataset_id=dataset_id,
            column_name=column_name,
            index_type="fulltext",
        ))
        db.commit()

    return True


# ─── Search ──────────────────────────────────────────────────────────────────


def _resolve_columns(
    db: Session,
    dataset_id: int,
    requested: list[str] | None,
) -> list[str]:
    if requested:
        return requested
    text_cols = get_text_columns(db, dataset_id)
    if text_cols:
        return text_cols
    rows = (
        db.query(DatasetColumn.column_name)
        .filter(DatasetColumn.dataset_id == dataset_id)
        .all()
    )
    return [r.column_name for r in rows]


def search_dataset(
    db: Session,
    dataset_id: int,
    query_str: str,
    columns: list[str] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> SearchResult:
    """
    Full-text search over dataset_rows for a given dataset.

    PostgreSQL handles stemming, stop words, and phrase matching via
    websearch_to_tsquery. ts_rank scores results by term frequency / length.
    Results are returned in descending relevance order, paginated.
    """
    parsed = parse_query(query_str)

    col_names = _resolve_columns(db, dataset_id, columns)
    if not col_names:
        return SearchResult(total=0, page=page, page_size=page_size, rows=[])

    tsvec = build_tsvector_sql(col_names)
    offset = (page - 1) * page_size
    params: dict[str, Any] = {
        "dataset_id": dataset_id,
        "config": FTS_CONFIG,
        "query": parsed.normalized,
    }

    label = f"fts ds={dataset_id}"

    total: int = timed_execute(
        db,
        text(f"""
            SELECT COUNT(*)
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
              AND {tsvec} @@ websearch_to_tsquery(:config, :query)
        """),
        params,
        label=f"{label} count",
    ).scalar() or 0

    if total == 0 or offset >= total:
        return SearchResult(total=int(total), page=page, page_size=page_size, rows=[])

    rows_raw = timed_execute(
        db,
        text(f"""
            SELECT
                id,
                data,
                ts_rank(
                    {tsvec},
                    websearch_to_tsquery(:config, :query),
                    32
                ) AS score
            FROM dataset_rows
            WHERE dataset_id = :dataset_id
              AND {tsvec} @@ websearch_to_tsquery(:config, :query)
            ORDER BY score DESC, id ASC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": offset},
        label=f"{label} rows",
    ).fetchall()

    return SearchResult(
        total=int(total),
        page=page,
        page_size=page_size,
        rows=[
            {"id": row.id, "data": row.data, "score": float(row.score)}
            for row in rows_raw
        ],
    )
