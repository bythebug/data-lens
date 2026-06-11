"""
Tests for full-text search: query parsing (pure), search_dataset (mock DB),
build_search_index, and the fts_integration helpers.
"""

from collections import namedtuple
from unittest.mock import MagicMock, call, patch

import pytest

from search.fts import _assert_safe_ident, build_tsvector_sql, fts_index_name
from search.engine import (
    ParsedQuery,
    QueryType,
    SearchResult,
    build_search_index,
    parse_query,
    search_dataset,
)


# ─── parse_query (pure, no DB) ───────────────────────────────────────────────


def test_parse_query_simple():
    pq = parse_query("hello world")
    assert pq.query_type == QueryType.SIMPLE
    assert pq.normalized == "hello world"
    assert "hello" in pq.terms
    assert "world" in pq.terms


def test_parse_query_single_word():
    pq = parse_query("python")
    assert pq.query_type == QueryType.SIMPLE
    assert pq.terms == ["python"]


def test_parse_query_phrase():
    pq = parse_query('"hello world"')
    assert pq.query_type == QueryType.PHRASE
    assert "hello world" in pq.terms  # phrase stripped of quotes


def test_parse_query_phrase_mixed():
    pq = parse_query('"data science" tools')
    assert pq.query_type == QueryType.PHRASE
    assert "data science" in pq.terms
    assert "tools" in pq.terms


def test_parse_query_boolean_and():
    pq = parse_query("python AND django")
    assert pq.query_type == QueryType.BOOLEAN
    assert "AND" not in pq.terms
    assert "python" in pq.terms
    assert "django" in pq.terms


def test_parse_query_boolean_or():
    pq = parse_query("python OR ruby")
    assert pq.query_type == QueryType.BOOLEAN
    assert "OR" not in pq.terms


def test_parse_query_boolean_takes_precedence_over_phrase():
    pq = parse_query('"data science" OR "machine learning"')
    assert pq.query_type == QueryType.BOOLEAN


def test_parse_query_negation_is_simple():
    pq = parse_query("python -django")
    assert pq.query_type == QueryType.SIMPLE
    assert "-django" in pq.terms


def test_parse_query_strips_whitespace():
    pq = parse_query("  hello  ")
    assert pq.normalized == "hello"


def test_parse_query_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_query("")


def test_parse_query_whitespace_only_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_query("   ")


# ─── fts_integration helpers (pure) ──────────────────────────────────────────


def test_build_tsvector_single_column():
    sql = build_tsvector_sql(["name"])
    assert "to_tsvector('english'" in sql
    assert "data->>'name'" in sql


def test_build_tsvector_multi_column():
    sql = build_tsvector_sql(["name", "bio"])
    assert "data->>'name'" in sql
    assert "data->>'bio'" in sql
    # Columns are concatenated
    assert "||" in sql


def test_build_tsvector_unsafe_column_raises():
    with pytest.raises(ValueError, match="Unsafe"):
        build_tsvector_sql(["name; DROP TABLE users--"])


def test_fts_index_name_format():
    name = fts_index_name(42, "city")
    assert name == "idx_fts_42_city"


def test_fts_index_name_sanitises_spaces():
    name = fts_index_name(1, "first name")
    assert " " not in name


def test_assert_safe_ident_valid():
    _assert_safe_ident("valid_name")
    _assert_safe_ident("Name123")


def test_assert_safe_ident_invalid():
    with pytest.raises(ValueError):
        _assert_safe_ident("bad-name")
    with pytest.raises(ValueError):
        _assert_safe_ident("name; DROP TABLE")
    with pytest.raises(ValueError):
        _assert_safe_ident("name'injection")


# ─── test_simple_search (mock DB) ────────────────────────────────────────────

Row = namedtuple("Row", ["id", "data", "score"])


def _make_db(count: int, rows: list) -> MagicMock:
    """Build a mock db.execute that returns count then rows."""
    db = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = count
    rows_result = MagicMock()
    rows_result.fetchall.return_value = rows
    db.execute.side_effect = [count_result, rows_result]
    return db


def _mock_columns(db: MagicMock, columns: list[str]) -> None:
    """Patch get_text_columns and DatasetColumn query used inside _resolve_columns."""
    import fts_integration
    import search

    with patch.object(search, "get_text_columns", return_value=columns):
        pass


def test_simple_search_returns_rows():
    fake_rows = [
        Row(id=1, data={"name": "Alice"}, score=0.9),
        Row(id=2, data={"name": "Bob"}, score=0.5),
    ]
    db = _make_db(count=2, rows=fake_rows)

    with patch("search.engine.get_text_columns", return_value=["name"]):
        result = search_dataset(db, dataset_id=1, query_str="alice")

    assert result.total == 2
    assert len(result.rows) == 2
    assert result.rows[0]["id"] == 1
    assert result.rows[0]["score"] == pytest.approx(0.9)


def test_no_results():
    db = _make_db(count=0, rows=[])

    with patch("search.engine.get_text_columns", return_value=["name"]):
        result = search_dataset(db, dataset_id=1, query_str="zzznomatch")

    assert result.total == 0
    assert result.rows == []
    # Should not execute a second query when count is 0
    assert db.execute.call_count == 1


def test_phrase_search():
    db = _make_db(count=1, rows=[Row(id=5, data={"bio": "data science"}, score=0.75)])

    with patch("search.engine.get_text_columns", return_value=["bio"]):
        result = search_dataset(db, dataset_id=1, query_str='"data science"')

    assert result.total == 1
    # The SQL call must have received the phrase query verbatim
    sql_call_args = db.execute.call_args_list[0]
    params = sql_call_args[0][1]
    assert params["query"] == '"data science"'


def test_boolean_operators():
    db = _make_db(count=3, rows=[
        Row(id=1, data={"tag": "python"}, score=0.8),
        Row(id=2, data={"tag": "ruby"}, score=0.7),
        Row(id=3, data={"tag": "python"}, score=0.6),
    ])

    with patch("search.engine.get_text_columns", return_value=["tag"]):
        result = search_dataset(db, dataset_id=1, query_str="python OR ruby")

    assert result.total == 3
    params = db.execute.call_args_list[0][0][1]
    assert "OR" in params["query"]


def test_relevance_ranking():
    # Results must come back in the order the DB returns them (score DESC)
    fake_rows = [
        Row(id=10, data={"text": "python python python"}, score=0.95),
        Row(id=20, data={"text": "python tutorial"}, score=0.60),
        Row(id=30, data={"text": "intro python"}, score=0.40),
    ]
    db = _make_db(count=3, rows=fake_rows)

    with patch("search.engine.get_text_columns", return_value=["text"]):
        result = search_dataset(db, dataset_id=1, query_str="python")

    scores = [r["score"] for r in result.rows]
    assert scores == sorted(scores, reverse=True)


def test_search_performance_uses_pagination():
    """search_dataset must pass LIMIT and OFFSET — never load all rows."""
    db = _make_db(count=1000, rows=[Row(id=i, data={}, score=0.5) for i in range(20)])

    with patch("search.engine.get_text_columns", return_value=["text"]):
        search_dataset(db, dataset_id=1, query_str="test", page=3, page_size=20)

    # Second execute call is the row fetch — must have limit and offset params
    row_params = db.execute.call_args_list[1][0][1]
    assert row_params["limit"] == 20
    assert row_params["offset"] == 40  # (page 3 - 1) * 20


def test_pagination_offset_beyond_total_returns_empty():
    db = _make_db(count=5, rows=[])

    with patch("search.engine.get_text_columns", return_value=["text"]):
        result = search_dataset(db, dataset_id=1, query_str="test", page=2, page_size=10)

    # offset (10) >= total (5) → no row fetch query
    assert result.rows == []
    assert db.execute.call_count == 1


def test_explicit_columns_override_text_columns():
    db = _make_db(count=1, rows=[Row(id=1, data={"notes": "hello"}, score=0.5)])

    with patch("search.engine.get_text_columns") as mock_get_text:
        search_dataset(db, dataset_id=1, query_str="hello", columns=["notes"])
        # get_text_columns should NOT be called when columns are explicit
        mock_get_text.assert_not_called()


# ─── build_search_index (mock DB) ────────────────────────────────────────────


def test_build_search_index_creates_index():
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None

    with (
        patch("search.engine.index_exists", return_value=False) as mock_exists,
        patch("search.engine.create_fts_index") as mock_create,
    ):
        created = build_search_index(db, dataset_id=7, column_name="bio")

    assert created is True
    mock_create.assert_called_once_with(db, 7, "bio")
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_build_search_index_skips_if_exists():
    db = MagicMock()

    with patch("search.engine.index_exists", return_value=True):
        created = build_search_index(db, dataset_id=7, column_name="bio")

    assert created is False
    db.add.assert_not_called()
