"""
Performance tests: slow-query logging, EXPLAIN plan parsing, index suggestion,
plan cache, and concurrent query safety.

Note: tests run against a mock DB — they verify the correctness of timing
wrappers, plan parsing logic, and index recommendation rules without requiring
a live PostgreSQL instance. Actual sub-second benchmarks require integration
tests against a seeded database.
"""

import logging
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from analytics.indexing_strategy import (
    IndexRecommendation,
    create_expression_index,
    drop_unused_indexes,
    suggest_indexes,
)
from analytics.optimizer import (
    QueryPlan,
    _PlanCache,
    _make_suggestions,
    _walk_plan,
    analyze_query,
    estimate_query_time,
    plan_cache,
    timed_execute,
)

# ─── timed_execute — slow query logging ──────────────────────────────────────


def _make_timed_db(result_mock: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute.return_value = result_mock
    return db


def test_slow_query_logs_warning(caplog):
    db = _make_timed_db(MagicMock())
    stmt = MagicMock()

    with caplog.at_level(logging.WARNING, logger="analytics.optimizer"):
        with patch("analytics.optimizer.time") as mock_time:
            mock_time.perf_counter.side_effect = [0.0, 1.5]  # 1.5s elapsed
            timed_execute(db, stmt, {}, label="test-query")

    assert any("Slow query" in r.message for r in caplog.records)
    assert any("test-query" in r.message for r in caplog.records)


def test_fast_query_does_not_warn(caplog):
    db = _make_timed_db(MagicMock())
    stmt = MagicMock()

    with caplog.at_level(logging.WARNING, logger="analytics.optimizer"):
        with patch("analytics.optimizer.time") as mock_time:
            mock_time.perf_counter.side_effect = [0.0, 0.05]  # 50ms
            timed_execute(db, stmt, {}, label="fast")

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_timed_execute_returns_result():
    result = MagicMock()
    result.scalar.return_value = 42
    db = _make_timed_db(result)

    with patch("analytics.optimizer.time") as mock_time:
        mock_time.perf_counter.side_effect = [0.0, 0.1]
        returned = timed_execute(db, MagicMock(), {})

    assert returned.scalar() == 42


def test_timed_execute_passes_params_to_db():
    db = _make_timed_db(MagicMock())
    stmt = MagicMock()
    params = {"dataset_id": 7, "query": "hello"}

    with patch("analytics.optimizer.time") as mock_time:
        mock_time.perf_counter.side_effect = [0.0, 0.1]
        timed_execute(db, stmt, params)

    db.execute.assert_called_once_with(stmt, params)


def test_query_under_1_second_threshold():
    """timed_execute threshold is exactly SLOW_QUERY_THRESHOLD_S (1.0s)."""
    from analytics.optimizer import SLOW_QUERY_THRESHOLD_S
    assert SLOW_QUERY_THRESHOLD_S == 1.0


# ─── EXPLAIN plan parsing (pure) ────────────────────────────────────────────


def _make_plan_node(node_type: str, relation: str = "", children: list = None) -> dict:
    node = {"Node Type": node_type}
    if relation:
        node["Relation Name"] = relation
    if children:
        node["Plans"] = children
    return node


def test_walk_plan_detects_seq_scan():
    seq_scans, index_scans = [], []
    node = _make_plan_node("Seq Scan", "dataset_rows")
    _walk_plan(node, seq_scans, index_scans)
    assert "dataset_rows" in seq_scans
    assert index_scans == []


def test_walk_plan_detects_index_scan():
    seq_scans, index_scans = [], []
    node = _make_plan_node("Index Scan", "dataset_rows")
    _walk_plan(node, seq_scans, index_scans)
    assert "dataset_rows" in index_scans
    assert seq_scans == []


def test_walk_plan_recurses_into_children():
    seq_scans, index_scans = [], []
    child = _make_plan_node("Seq Scan", "dataset_rows")
    parent = _make_plan_node("Aggregate", children=[child])
    _walk_plan(parent, seq_scans, index_scans)
    assert "dataset_rows" in seq_scans


def test_walk_plan_mixed():
    seq_scans, index_scans = [], []
    _walk_plan(
        _make_plan_node("Hash Join", children=[
            _make_plan_node("Seq Scan", "users"),
            _make_plan_node("Index Scan", "dataset_rows"),
        ]),
        seq_scans, index_scans,
    )
    assert "users" in seq_scans
    assert "dataset_rows" in index_scans


def test_make_suggestions_for_seq_scans():
    suggestions = _make_suggestions(["dataset_rows", "dataset_rows", "users"])
    assert len(suggestions) == 2  # deduplicated
    assert any("dataset_rows" in s for s in suggestions)
    assert any("users" in s for s in suggestions)


def test_make_suggestions_empty_for_no_seq_scans():
    assert _make_suggestions([]) == []


def test_analyze_query_parses_plan(monkeypatch):
    fake_plan = [{
        "Plan": {
            "Node Type": "Aggregate",
            "Plan Rows": 500,
            "Plans": [{"Node Type": "Seq Scan", "Relation Name": "dataset_rows"}],
        },
        "Execution Time": 42.5,
        "Planning Time": 1.2,
    }]

    db = MagicMock()
    db.execute.return_value.fetchone.return_value = [fake_plan]

    monkeypatch.setattr("analytics.optimizer.plan_cache", _PlanCache())  # fresh cache

    result = analyze_query(db, "SELECT 1", use_cache=False)

    assert result.execution_time_ms == pytest.approx(42.5)
    assert result.planning_time_ms == pytest.approx(1.2)
    assert result.rows_estimated == 500
    assert "dataset_rows" in result.seq_scans
    assert len(result.suggestions) == 1


def test_analyze_query_index_scan(monkeypatch):
    fake_plan = [{
        "Plan": {
            "Node Type": "Index Scan",
            "Relation Name": "dataset_rows",
            "Plan Rows": 10,
        },
        "Execution Time": 2.1,
        "Planning Time": 0.5,
    }]

    db = MagicMock()
    db.execute.return_value.fetchone.return_value = [fake_plan]

    result = analyze_query(db, "SELECT 1", use_cache=False)

    assert "dataset_rows" in result.index_scans
    assert result.seq_scans == []
    assert result.suggestions == []


def test_estimate_query_time():
    fake_plan = [{"Execution Time": 15.3}]
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = [fake_plan]

    ms = estimate_query_time(db, "SELECT 1")
    assert ms == pytest.approx(15.3)


# ─── Plan cache ──────────────────────────────────────────────────────────────


def test_plan_cache_stores_and_retrieves():
    cache = _PlanCache(maxsize=10, ttl_seconds=60)
    cache.set("SELECT 1", {"execution_time_ms": 5.0})
    result = cache.get("SELECT 1")
    assert result["execution_time_ms"] == 5.0


def test_plan_cache_miss_returns_none():
    cache = _PlanCache()
    assert cache.get("nonexistent") is None


def test_plan_cache_ttl_expiry():
    cache = _PlanCache(ttl_seconds=0.01)
    cache.set("SELECT 1", {"data": "fresh"})
    time.sleep(0.02)
    assert cache.get("SELECT 1") is None


def test_plan_cache_lru_eviction():
    cache = _PlanCache(maxsize=2)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    cache.set("c", {"v": 3})    # should evict "a" (LRU)
    assert cache.get("a") is None
    assert cache.get("b") is not None


def test_analyze_query_uses_cache(monkeypatch):
    fake_plan = [{"Plan": {"Plan Rows": 1}, "Execution Time": 5.0, "Planning Time": 0.1}]
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = [fake_plan]

    fresh = _PlanCache()
    monkeypatch.setattr("analytics.optimizer.plan_cache", fresh)

    analyze_query(db, "SELECT cached", use_cache=True)
    analyze_query(db, "SELECT cached", use_cache=True)  # second call hits cache

    # db.execute should only be called once (cache hit on second call)
    assert db.execute.call_count == 1


# ─── suggest_indexes (mock DB) ───────────────────────────────────────────────


def _make_cols_db(cols: list[tuple[str, str]], distinct_ratio: float = 0.8) -> MagicMock:
    """Build a mock DB with DatasetColumn rows and a cardinality scalar."""
    db = MagicMock()

    class FakeCol:
        def __init__(self, name, dtype):
            self.column_name = name
            self.data_type = dtype

    db.query.return_value.filter.return_value.all.side_effect = [
        [FakeCol(n, t) for n, t in cols],  # DatasetColumn query
        [],  # SearchIndex query (no existing FTS)
    ]
    db.execute.return_value.scalar.return_value = distinct_ratio
    return db


def test_suggest_indexes_numeric_column():
    db = _make_cols_db([("age", "numeric")])
    recs = suggest_indexes(db, dataset_id=1)
    btree = [r for r in recs if r.column_name == "age" and r.index_type == "btree"]
    assert btree, "Expected btree recommendation for numeric column"


def test_suggest_indexes_date_column():
    db = _make_cols_db([("joined", "date")])
    recs = suggest_indexes(db, dataset_id=1)
    assert any(r.column_name == "joined" and r.index_type == "btree" for r in recs)


def test_suggest_indexes_high_cardinality_text():
    db = _make_cols_db([("email", "text")], distinct_ratio=0.95)
    recs = suggest_indexes(db, dataset_id=1)
    assert any(r.column_name == "email" and r.index_type == "hash" for r in recs)


def test_suggest_indexes_skips_low_cardinality_text():
    db = _make_cols_db([("status", "text")], distinct_ratio=0.02)
    recs = suggest_indexes(db, dataset_id=1)
    status_recs = [r for r in recs if r.column_name == "status"]
    assert not status_recs, "Low-cardinality text should not be indexed"


def test_suggest_indexes_skips_existing_fts():
    db = MagicMock()

    class FakeCol:
        column_name = "bio"
        data_type = "text"

    class FakeSI:
        column_name = "bio"
        index_type = "fulltext"

    db.query.return_value.filter.return_value.all.side_effect = [
        [FakeCol()],   # DatasetColumn query
        [FakeSI()],    # SearchIndex query — FTS already exists
    ]
    recs = suggest_indexes(db, dataset_id=1)
    assert not any(r.column_name == "bio" for r in recs)


def test_suggest_indexes_composite_date_numeric():
    db = _make_cols_db([("revenue", "numeric"), ("joined", "date")])
    recs = suggest_indexes(db, dataset_id=1)
    composite = [r for r in recs if r.composite_with]
    assert composite, "Expected composite index recommendation"


def test_index_effectiveness_numeric_prefers_btree():
    db = _make_cols_db([("price", "numeric"), ("quantity", "numeric"), ("score", "numeric")])
    recs = suggest_indexes(db, dataset_id=1)
    btree_cols = {r.column_name for r in recs if r.index_type == "btree" and not r.composite_with}
    assert "price" in btree_cols
    assert "quantity" in btree_cols


# ─── create_expression_index (mock DB) ───────────────────────────────────────


def test_create_expression_index_calls_ddl():
    db = MagicMock()
    idx = create_expression_index(db, dataset_id=5, column_name="age", col_type="numeric")
    assert idx == "idx_btree_5_age"
    db.execute.assert_called_once()
    db.commit.assert_called_once()


def test_create_expression_index_unsafe_column_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="Unsafe"):
        create_expression_index(db, 1, "age; DROP TABLE users--", "numeric")


def test_create_expression_index_unsupported_type_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="Unsupported index type"):
        create_expression_index(db, 1, "age", "numeric", index_type="gist")


# ─── drop_unused_indexes (mock DB) ───────────────────────────────────────────


def test_drop_unused_indexes_calls_drop():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        ("idx_btree_7_age",),
        ("idx_hash_7_city",),
    ]
    dropped = drop_unused_indexes(db, dataset_id=7, min_scans=0)

    assert set(dropped) == {"idx_btree_7_age", "idx_hash_7_city"}
    assert db.commit.called


def test_drop_unused_indexes_none_to_drop():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    dropped = drop_unused_indexes(db, dataset_id=7)

    assert dropped == []
    db.commit.assert_not_called()


# ─── complex aggregation performance (timing) ────────────────────────────────


def test_complex_aggregation_uses_timed_execute():
    """aggregate_dataset must call timed_execute, not db.execute directly."""
    from analytics.aggregation import aggregate_dataset, Metric

    db = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    with (
        patch("analytics.aggregation.timed_execute", return_value=mock_result) as mock_te,
        patch("analytics.aggregation.get_column_types", return_value={"region": "text", "revenue": "numeric"}),
        patch("analytics.aggregation.build_where_clause", return_value=("TRUE", {})),
    ):
        aggregate_dataset(db, 1, ["region"], [Metric("SUM", "revenue")])

    mock_te.assert_called_once()
    call_kwargs = mock_te.call_args
    assert "aggregate" in call_kwargs[1].get("label", "") or "aggregate" in str(call_kwargs)


def test_fts_search_uses_timed_execute():
    """search_dataset must call timed_execute for both count and row queries."""
    from search.engine import search_dataset

    count_result = MagicMock()
    count_result.scalar.return_value = 0

    with (
        patch("search.engine.timed_execute", return_value=count_result) as mock_te,
        patch("search.engine.get_text_columns", return_value=["bio"]),
    ):
        search_dataset(MagicMock(), dataset_id=1, query_str="hello")

    assert mock_te.call_count >= 1


# ─── test_concurrent_queries ─────────────────────────────────────────────────


def test_concurrent_queries_thread_safe():
    """Multiple threads calling timed_execute simultaneously must not interfere."""
    results = []
    errors = []

    def run_query(thread_id: int) -> None:
        try:
            result_mock = MagicMock()
            result_mock.scalar.return_value = thread_id * 10
            db = MagicMock()
            db.execute.return_value = result_mock

            with patch("analytics.optimizer.time") as mock_time:
                mock_time.perf_counter.side_effect = [0.0, 0.05]
                val = timed_execute(db, MagicMock(), {}, label=f"t{thread_id}").scalar()
            results.append(val)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_query, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 10


def test_plan_cache_concurrent_writes():
    """Plan cache must not corrupt under concurrent access."""
    cache = _PlanCache(maxsize=50)
    errors = []

    def write_cache(i: int) -> None:
        try:
            cache.set(f"sql_{i}", {"execution_time_ms": float(i)})
            val = cache.get(f"sql_{i}")
            if val is not None:
                assert val["execution_time_ms"] == float(i)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_cache, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
