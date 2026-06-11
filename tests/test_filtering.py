"""
Tests for query_builder: build_where_clause, execute_query, parse_filter_input.
All pure-logic tests need no DB; execute_query tests use a mock Session.
"""

import json
from collections import namedtuple
from unittest.mock import MagicMock

import pytest

from analytics.query_builder import (
    Filter,
    FilterGroup,
    LogicOp,
    QueryResult,
    assert_safe_column,
    assert_valid_operator,
    build_where_clause,
    execute_query,
    parse_filter_input,
)

TYPES = {"age": "numeric", "city": "text", "joined": "date", "score": "numeric"}

Row = namedtuple("Row", ["id", "data"])


def _make_db(count: int, rows: list) -> MagicMock:
    db = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = count
    rows_result = MagicMock()
    rows_result.fetchall.return_value = rows
    db.execute.side_effect = [count_result, rows_result]
    return db


# ─── build_where_clause (pure) ────────────────────────────────────────────────


def test_filter_equal():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "=", "NYC")]), TYPES
    )
    assert "(data->>'city') = :p_0" in sql
    assert params == {"p_0": "NYC"}


def test_filter_greater_than():
    sql, params = build_where_clause(
        FilterGroup([Filter("age", ">", 30)]), TYPES
    )
    assert "(data->>'age')::numeric > :p_0" in sql
    assert params["p_0"] == 30


def test_filter_less_than():
    sql, params = build_where_clause(
        FilterGroup([Filter("age", "<", 18)]), TYPES
    )
    assert "< :p_0" in sql


def test_filter_gte_lte():
    sql, params = build_where_clause(
        FilterGroup([Filter("score", ">=", 90)]), TYPES
    )
    assert ">= :p_0" in sql
    assert "(data->>'score')::numeric" in sql


def test_filter_not_equal():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "!=", "LA")]), TYPES
    )
    assert "!= :p_0" in sql
    assert params["p_0"] == "LA"


def test_in_operator():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "IN", ["NYC", "LA", "Chicago"])]), TYPES
    )
    assert "IN (:p_0, :p_1, :p_2)" in sql
    assert params == {"p_0": "NYC", "p_1": "LA", "p_2": "Chicago"}


def test_in_operator_empty_list_produces_false():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "IN", [])]), TYPES
    )
    assert sql == "FALSE"
    assert params == {}


def test_like_operator():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "LIKE", "New%")]), TYPES
    )
    assert "(data->>'city') LIKE :p_0" in sql
    assert params["p_0"] == "New%"


def test_ilike_operator():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "ILIKE", "%york%")]), TYPES
    )
    assert "ILIKE :p_0" in sql


def test_is_null():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "IS NULL")]), TYPES
    )
    assert "data->>'city' IS NULL" in sql
    assert params == {}


def test_is_not_null():
    sql, params = build_where_clause(
        FilterGroup([Filter("city", "IS NOT NULL")]), TYPES
    )
    assert "IS NOT NULL" in sql


def test_multiple_filters_and():
    sql, params = build_where_clause(
        FilterGroup(
            [Filter("age", ">", 18), Filter("city", "=", "NYC")],
            logic=LogicOp.AND,
        ),
        TYPES,
    )
    assert " AND " in sql
    assert len(params) == 2


def test_multiple_filters_or():
    sql, params = build_where_clause(
        FilterGroup(
            [Filter("city", "=", "NYC"), Filter("city", "=", "LA")],
            logic=LogicOp.OR,
        ),
        TYPES,
    )
    assert " OR " in sql


def test_empty_filter_group_returns_true():
    sql, params = build_where_clause(FilterGroup([]), TYPES)
    assert sql == "TRUE"
    assert params == {}


def test_date_column_cast():
    sql, _ = build_where_clause(
        FilterGroup([Filter("joined", ">", "2024-01-01")]), TYPES
    )
    assert "(data->>'joined')::date" in sql


def test_unsafe_column_raises():
    with pytest.raises(ValueError, match="Unsafe"):
        build_where_clause(
            FilterGroup([Filter("age; DROP TABLE users--", ">", 30)]), TYPES
        )


def test_invalid_operator_raises():
    with pytest.raises(ValueError, match="Unknown operator"):
        build_where_clause(
            FilterGroup([Filter("age", "BETWEEN", 10)]), TYPES
        )


# ─── assert helpers (pure) ───────────────────────────────────────────────────


def test_assert_safe_column_valid():
    assert_safe_column("valid_col")
    assert_safe_column("Col123")
    assert_safe_column("_private")


def test_assert_safe_column_invalid():
    for bad in ["bad-name", "col'injection", "col; DROP TABLE", "1starts_with_num"]:
        with pytest.raises(ValueError):
            assert_safe_column(bad)


def test_assert_valid_operator_valid():
    for op in ["=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "ILIKE", "IS NULL", "IS NOT NULL"]:
        assert_valid_operator(op)


def test_assert_valid_operator_invalid():
    with pytest.raises(ValueError):
        assert_valid_operator("BETWEEN")


# ─── parse_filter_input (pure) ────────────────────────────────────────────────


def test_parse_filter_input_json_string():
    raw = json.dumps([{"column": "age", "operator": ">", "value": 30}])
    fg = parse_filter_input(raw)
    assert len(fg.filters) == 1
    assert fg.filters[0].column == "age"
    assert fg.filters[0].operator == ">"
    assert fg.filters[0].value == 30


def test_parse_filter_input_list():
    fg = parse_filter_input([{"column": "city", "operator": "=", "value": "NYC"}])
    assert fg.filters[0].column == "city"


def test_parse_filter_input_with_logic():
    raw = {"logic": "OR", "filters": [
        {"column": "city", "operator": "=", "value": "NYC"},
        {"column": "city", "operator": "=", "value": "LA"},
    ]}
    fg = parse_filter_input(raw)
    assert fg.logic == LogicOp.OR
    assert len(fg.filters) == 2


def test_parse_filter_input_defaults_to_and():
    fg = parse_filter_input([{"column": "age", "operator": ">", "value": 18}])
    assert fg.logic == LogicOp.AND


# ─── execute_query (mock DB) ─────────────────────────────────────────────────


def test_execute_query_no_filters():
    db = _make_db(count=2, rows=[Row(1, {"age": 25}), Row(2, {"age": 30})])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.query_builder.get_column_types", lambda *a: TYPES)
        result = execute_query(db, dataset_id=1)
    assert result.total == 2
    assert len(result.rows) == 2


def test_execute_query_with_filter():
    db = _make_db(count=1, rows=[Row(5, {"age": 35, "city": "NYC"})])
    fg = FilterGroup([Filter("age", ">", 30)])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.query_builder.get_column_types", lambda *a: TYPES)
        result = execute_query(db, dataset_id=1, filter_group=fg, column_types=TYPES)
    assert result.total == 1
    assert result.rows[0]["id"] == 5


def test_execute_query_no_results():
    db = _make_db(count=0, rows=[])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.query_builder.get_column_types", lambda *a: TYPES)
        result = execute_query(db, dataset_id=1)
    assert result.total == 0
    assert result.rows == []
    assert db.execute.call_count == 1  # no row-fetch when count is 0


def test_execute_query_pagination():
    db = _make_db(count=100, rows=[Row(i, {}) for i in range(10)])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.query_builder.get_column_types", lambda *a: TYPES)
        execute_query(db, dataset_id=1, page=3, page_size=10, column_types=TYPES)
    row_params = db.execute.call_args_list[1][0][1]
    assert row_params["limit"] == 10
    assert row_params["offset"] == 20


def test_execute_query_sort_by():
    db = _make_db(count=5, rows=[Row(i, {}) for i in range(5)])
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.query_builder.get_column_types", lambda *a: TYPES)
        execute_query(db, dataset_id=1, sort_by="age", sort_dir="DESC", column_types=TYPES)
    # Sort expression must appear in the SQL sent to db.execute
    sql_str = str(db.execute.call_args_list[1][0][0])
    assert "age" in sql_str.lower()
