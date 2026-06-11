"""
PostgreSQL full-text search helpers: index DDL, tsvector SQL, and column resolution.
All functions that touch DDL validate identifiers before interpolating them.
"""

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

FTS_CONFIG = "english"

_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _assert_safe_ident(name: str) -> None:
    if not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")


def fts_index_name(dataset_id: int, column_name: str) -> str:
    safe_col = re.sub(r"[^a-zA-Z0-9]", "_", column_name).lower()
    return f"idx_fts_{dataset_id}_{safe_col}"


def build_tsvector_sql(column_names: list[str], config: str = FTS_CONFIG) -> str:
    """
    Return a SQL fragment that computes a tsvector over one or more JSONB keys.
    Example (single col):  to_tsvector('english', coalesce(data->>'name', ''))
    Example (multi col):   to_tsvector('english', coalesce(data->>'name', '') || chr(32) || coalesce(data->>'bio', ''))
    """
    for col in column_names:
        _assert_safe_ident(col)
    parts = [f"coalesce(data->>'{col}', '')" for col in column_names]
    return f"to_tsvector('{config}', {' || chr(32) || '.join(parts)})"


def create_fts_index(db: Session, dataset_id: int, column_name: str) -> str:
    """
    Create a GIN functional index on to_tsvector(data->>'column_name')
    scoped to dataset_id. Safe: dataset_id is int, column_name is validated.
    Returns the index name.
    """
    _assert_safe_ident(column_name)
    idx = fts_index_name(dataset_id, column_name)
    tsvec = build_tsvector_sql([column_name])

    db.execute(text(f"""
        CREATE INDEX IF NOT EXISTS {idx}
        ON dataset_rows USING GIN (({tsvec}))
        WHERE dataset_id = {int(dataset_id)}
    """))
    db.commit()
    return idx


def drop_fts_index(db: Session, dataset_id: int, column_name: str) -> None:
    _assert_safe_ident(column_name)
    idx = fts_index_name(dataset_id, column_name)
    db.execute(text(f"DROP INDEX IF EXISTS {idx}"))
    db.commit()


def index_exists(db: Session, dataset_id: int, column_name: str) -> bool:
    idx = fts_index_name(dataset_id, column_name)
    row = db.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
        {"name": idx},
    ).fetchone()
    return row is not None


def get_text_columns(db: Session, dataset_id: int) -> list[str]:
    from db.models import DatasetColumn

    rows = (
        db.query(DatasetColumn.column_name)
        .filter(
            DatasetColumn.dataset_id == dataset_id,
            DatasetColumn.data_type == "text",
        )
        .all()
    )
    return [r.column_name for r in rows]
