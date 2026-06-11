"""
Tests for aggregation: parse_metric, aggregate_dataset, time_series_aggregate,
column_stats, and dataset_stats.
"""

from collections import namedtuple
from unittest.mock import MagicMock

import pytest

from analytics.aggregation import (
    Metric,
    aggregate_dataset,
    parse_metric,
    time_series_aggregate,
)
from analytics.stats import column_stats, dataset_stats

TYPES = {"revenue": "numeric", "region": "text", "price": "numeric", "joined": "date", "tag": "text"}


# ─── parse_metric (pure) ─────────────────────────────────────────────────────


def test_parse_metric_sum():
    m = parse_metric("SUM(revenue)")
    assert m.function == "SUM"
    assert m.column == "revenue"
    assert m.alias == "sum_revenue"


def test_parse_metric_count_star():
    m = parse_metric("COUNT(*)")
    assert m.function == "COUNT"
    assert m.column == "*"
    assert m.alias == "count_all"


def test_parse_metric_avg():
    m = parse_metric("AVG(price)")
    assert m.function == "AVG"
    assert m.alias == "avg_price"


def test_parse_metric_stddev():
    m = parse_metric("STDDEV(score)")
    assert m.function == "STDDEV"
    assert m.alias == "stddev_score"


def test_parse_metric_min_max():
    assert parse_metric("MIN(age)").function == "MIN"
    assert parse_metric("MAX(age)").function == "MAX"


def test_parse_metric_case_insensitive():
    m = parse_metric("sum(revenue)")
    assert m.function == "SUM"


def test_parse_metric_invalid_raises():
    with pytest.raises(ValueError, match="Invalid metric"):
        parse_metric("revenue")  # missing function

    with pytest.raises(ValueError, match="Invalid metric"):
        parse_metric("SUM()")   # missing column

    with pytest.raises(ValueError, match="Invalid metric"):
        parse_metric("MEDIAN(age)")  # unsupported function


def test_parse_metric_unsafe_column_raises():
    with pytest.raises(ValueError):
        parse_metric("SUM(revenue; DROP TABLE)")


# ─── aggregate_dataset (mock DB) ─────────────────────────────────────────────

class _AggRow:
    """Fake SQLAlchemy row with a ._mapping dict, as aggregate_dataset expects."""
    def __init__(self, **kwargs):
        self._mapping = kwargs


def _make_agg_db(rows: list) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    db.execute.return_value = result
    return db


def _agg_row(**kwargs):
    return _AggRow(**kwargs)


def test_group_by():
    rows = [
        _agg_row(region="East", sum_revenue=50000.0),
        _agg_row(region="West", sum_revenue=30000.0),
    ]
    db = _make_agg_db(rows)
    metrics = [Metric("SUM", "revenue")]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
        mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
        result = aggregate_dataset(db, dataset_id=1, group_by=["region"], metrics=metrics)

    assert len(result) == 2
    assert result[0]["region"] == "East"


def test_sum_metric():
    rows = [_agg_row(region="East", sum_revenue=50000.0)]
    db = _make_agg_db(rows)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
        mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
        result = aggregate_dataset(
            db, dataset_id=1,
            group_by=["region"],
            metrics=[Metric("SUM", "revenue")],
        )

    assert result[0]["sum_revenue"] == 50000.0


def test_multiple_metrics():
    rows = [_agg_row(region="East", sum_revenue=50000.0, avg_price=120.5)]
    db = _make_agg_db(rows)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
        mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
        result = aggregate_dataset(
            db, dataset_id=1,
            group_by=["region"],
            metrics=[Metric("SUM", "revenue"), Metric("AVG", "price")],
        )

    assert "sum_revenue" in result[0]
    assert "avg_price" in result[0]


def test_empty_results():
    db = _make_agg_db([])

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
        mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
        result = aggregate_dataset(
            db, dataset_id=1,
            group_by=["region"],
            metrics=[Metric("COUNT", "*")],
        )

    assert result == []


def test_aggregate_no_metrics_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="metric"):
        aggregate_dataset(db, dataset_id=1, group_by=["region"], metrics=[])


def test_aggregate_no_group_by_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="group_by"):
        aggregate_dataset(db, dataset_id=1, group_by=[], metrics=[Metric("COUNT", "*")])


# ─── time_series_aggregate (mock DB) ─────────────────────────────────────────

class _TSRow:
    """Fake SQLAlchemy row for time_series_aggregate: has .period and ._mapping."""
    def __init__(self, period, alias, value):
        self.period = period
        self._mapping = {alias: value}


def _ts_row(period, alias, value):
    return _TSRow(period, alias, value)


def test_time_series_aggregation():
    rows = [
        _ts_row("2024-01-01", "sum_revenue", 10000.0),
        _ts_row("2024-02-01", "sum_revenue", 12000.0),
        _ts_row("2024-03-01", "sum_revenue", 9500.0),
    ]
    db = _make_agg_db(rows)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
        mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
        result = time_series_aggregate(
            db, dataset_id=1,
            date_column="joined",
            metric=Metric("SUM", "revenue"),
            truncate="month",
        )

    assert len(result) == 3
    assert result[0]["period"] == "2024-01-01"
    assert result[0]["sum_revenue"] == 10000.0


def test_time_series_invalid_truncation_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="Invalid truncation"):
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
            time_series_aggregate(
                db, dataset_id=1,
                date_column="joined",
                metric=Metric("COUNT", "*"),
                truncate="decade",
            )


def test_time_series_valid_truncations():
    for trunc in ("day", "week", "month", "quarter", "year"):
        rows = []
        db = _make_agg_db(rows)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
            mp.setattr("analytics.aggregation.build_where_clause", lambda *a, **kw: ("TRUE", {}))
            result = time_series_aggregate(
                db, dataset_id=1,
                date_column="joined",
                metric=Metric("COUNT", "*"),
                truncate=trunc,
            )
        assert result == []


def test_time_series_unsafe_column_raises():
    db = MagicMock()
    with pytest.raises(ValueError, match="Unsafe"):
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("analytics.aggregation.get_column_types", lambda *a: TYPES)
            time_series_aggregate(
                db, dataset_id=1,
                date_column="date; DROP TABLE users--",
                metric=Metric("COUNT", "*"),
            )


# ─── column_stats / dataset_stats (mock DB) ──────────────────────────────────

StatRow = namedtuple("StatRow", [
    "total_count", "non_null_count", "null_count", "distinct_count",
])
NumRow = namedtuple("NumRow", ["min_val", "max_val", "avg_val", "stddev_val"])
TextRow = namedtuple("TextRow", ["min_length", "max_length", "avg_length"])
DateRow = namedtuple("DateRow", ["min_date", "max_date"])


def _make_stats_db(*side_effects) -> MagicMock:
    db = MagicMock()
    results = []
    for row in side_effects:
        r = MagicMock()
        r.fetchone.return_value = row
        results.append(r)
    db.execute.side_effect = results
    return db


def test_column_stats_numeric():
    db = _make_stats_db(
        StatRow(100, 95, 5, 45),
        NumRow(18.0, 85.0, 35.2, 12.1),
    )
    result = column_stats(db, dataset_id=1, column_name="age", col_type="numeric")

    assert result["column"] == "age"
    assert result["total_count"] == 100
    assert result["null_count"] == 5
    assert result["min"] == 18.0
    assert result["max"] == 85.0
    assert result["avg"] == pytest.approx(35.2)
    assert result["stddev"] == pytest.approx(12.1)


def test_column_stats_text():
    db = _make_stats_db(
        StatRow(50, 48, 2, 12),
        TextRow(2, 20, 8.5),
    )
    result = column_stats(db, dataset_id=1, column_name="city", col_type="text")

    assert result["distinct_count"] == 12
    assert result["min_length"] == 2
    assert result["max_length"] == 20
    assert result["avg_length"] == pytest.approx(8.5)


def test_column_stats_date():
    db = _make_stats_db(
        StatRow(200, 198, 2, 150),
        DateRow("2020-01-01", "2024-12-31"),
    )
    result = column_stats(db, dataset_id=1, column_name="joined", col_type="date")

    assert result["min_date"] == "2020-01-01"
    assert result["max_date"] == "2024-12-31"


def test_column_stats_null_numeric():
    db = _make_stats_db(
        StatRow(10, 0, 10, 0),
        NumRow(None, None, None, None),
    )
    result = column_stats(db, dataset_id=1, column_name="score", col_type="numeric")

    assert result["min"] is None
    assert result["max"] is None


def test_dataset_stats_calls_each_column():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "analytics.stats.get_column_types",
            lambda *a: {"age": "numeric", "city": "text"},
        )
        mp.setattr(
            "analytics.stats.column_stats",
            lambda db, ds_id, col, typ: {"column": col, "data_type": typ},
        )
        result = dataset_stats(MagicMock(), dataset_id=1)

    assert len(result) == 2
    cols = {r["column"] for r in result}
    assert cols == {"age", "city"}
