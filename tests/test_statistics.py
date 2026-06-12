"""
Tests for statistical analysis, time-series operations, and export.
Pure numpy computations are tested with known inputs and expected outputs.
DB-dependent functions use mock Sessions.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from analytics.export import export_to_csv, export_to_json, flatten_row
from analytics.statistics import (
    basic_stats,
    correlation_matrix,
    distribution_analysis,
    outlier_detection,
    quantiles,
)
from analytics.time_series import growth_rate, moving_average, resample_by_period

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_value_db(values: list[float]) -> MagicMock:
    """Mock DB whose first execute returns a column of numeric values."""
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [(v,) for v in values]
    return db


def _make_multi_db(*side_effects) -> MagicMock:
    """Mock DB with multiple execute return values."""
    db = MagicMock()
    results = []
    for rows in side_effects:
        r = MagicMock()
        r.fetchall.return_value = rows
        results.append(r)
    db.execute.side_effect = results
    return db


# ─── basic_stats ─────────────────────────────────────────────────────────────


def test_basic_stats_correct_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    db = _make_value_db(values)
    result = basic_stats(db, dataset_id=1, column="age")

    assert result["count"] == 10
    assert result["mean"] == pytest.approx(5.5)
    assert result["median"] == pytest.approx(5.5)
    assert result["min"] == pytest.approx(1.0)
    assert result["max"] == pytest.approx(10.0)
    assert result["std"] == pytest.approx(np.std(values, ddof=1))
    assert result["variance"] == pytest.approx(np.var(values, ddof=1))
    assert "skewness" in result
    assert "kurtosis" in result


def test_basic_stats_empty_returns_none():
    db = _make_value_db([])
    assert basic_stats(db, dataset_id=1, column="age") is None


def test_basic_stats_single_value():
    db = _make_value_db([42.0])
    result = basic_stats(db, dataset_id=1, column="score")
    assert result["count"] == 1
    assert result["mean"] == pytest.approx(42.0)
    assert result["min"] == result["max"] == pytest.approx(42.0)


def test_basic_stats_skewness_positive():
    # Right-skewed distribution
    values = [1, 1, 1, 2, 2, 3, 10, 20, 50]
    db = _make_value_db(values)
    result = basic_stats(db, dataset_id=1, column="x")
    assert result["skewness"] > 0


def test_basic_stats_symmetric_zero_skew():
    values = list(range(-5, 6))  # symmetric around 0
    db = _make_value_db(values)
    result = basic_stats(db, dataset_id=1, column="x")
    assert abs(result["skewness"]) < 0.01


# ─── quantiles ───────────────────────────────────────────────────────────────


def test_quantiles_known_values():
    values = list(range(1, 101))  # 1..100
    db = _make_value_db(values)
    result = quantiles(db, dataset_id=1, column="score")

    assert result["p25"] == pytest.approx(25.75)
    assert result["p50"] == pytest.approx(50.5)
    assert result["p75"] == pytest.approx(75.25)
    assert result["p90"] == pytest.approx(90.1)
    assert result["p99"] == pytest.approx(99.01)


def test_quantiles_empty_returns_none():
    db = _make_value_db([])
    assert quantiles(db, dataset_id=1, column="x") is None


def test_quantiles_ordering():
    db = _make_value_db([5, 1, 3, 9, 7, 2, 8, 4, 6, 10])
    result = quantiles(db, dataset_id=1, column="x")
    assert result["p25"] <= result["p50"] <= result["p75"] <= result["p90"] <= result["p99"]


# ─── outlier_detection ───────────────────────────────────────────────────────


def test_outlier_detection_iqr_bounds():
    # Q1=25, Q3=75, IQR=50 → fences [-50, 150]
    values = list(range(1, 101))
    db = _make_multi_db(
        [(v,) for v in values],   # _fetch_values
        [],                        # outlier rows query
    )
    result = outlier_detection(db, dataset_id=1, column="score")

    assert result["method"] == "IQR (Tukey fences)"
    # np.percentile(range(1,101), [25,75]) → [25.75, 75.25], IQR = 49.5
    assert result["iqr"] == pytest.approx(49.5, abs=0.1)
    assert result["lower_fence"] < result["q1"]
    assert result["upper_fence"] > result["q3"]


def test_outlier_detection_finds_outliers():
    # 1-10 with a clear outlier at 1000
    values = list(range(1, 11))
    OutlierRow = type("Row", (), {"id": 99, "data": {"score": 1000}})

    db = _make_multi_db(
        [(v,) for v in values],
        [OutlierRow()],
    )
    result = outlier_detection(db, dataset_id=1, column="score")
    assert result["outlier_count"] == 1
    assert result["outliers"][0]["id"] == 99


def test_outlier_detection_no_outliers():
    values = list(range(1, 11))
    db = _make_multi_db([(v,) for v in values], [])
    result = outlier_detection(db, dataset_id=1, column="score")
    assert result["outlier_count"] == 0
    assert result["outliers"] == []


def test_outlier_detection_empty_returns_none():
    db = _make_value_db([])
    assert outlier_detection(db, dataset_id=1, column="x") is None


# ─── correlation_matrix ──────────────────────────────────────────────────────

ColTypeRow = type("Row", (), {})


def _make_col_type_db(col_types: dict, rows: list) -> MagicMock:
    """Mock DB for correlation_matrix: returns column types + data rows."""
    db = MagicMock()

    class ColRow:
        def __init__(self, name, dtype):
            self.column_name = name
            self.data_type = dtype

    col_rows = [ColRow(n, t) for n, t in col_types.items()]

    # First call: get_column_types → DatasetColumn query
    col_result = MagicMock()
    col_result.all.return_value = col_rows

    # Second call: the actual data rows
    data_result = MagicMock()
    data_result.fetchall.return_value = rows

    db.query.return_value.filter.return_value.all.return_value = col_rows
    db.execute.return_value.fetchall.return_value = rows
    return db


def test_correlation_calculation():
    # Perfect positive correlation: y = 2x
    class FakeRow:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    rows = [FakeRow(float(i), float(2 * i)) for i in range(1, 21)]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "analytics.statistics.get_column_types",
            lambda *a: {"x": "numeric", "y": "numeric"},
        )
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows

        result = correlation_matrix(db, dataset_id=1)

    assert result["columns"] == ["x", "y"]
    corr_xy = result["matrix"][0]["correlations"]["y"]
    assert corr_xy == pytest.approx(1.0, abs=0.001)


def test_correlation_negative():
    class FakeRow:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    rows = [FakeRow(float(i), float(-i)) for i in range(1, 21)]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "analytics.statistics.get_column_types",
            lambda *a: {"x": "numeric", "y": "numeric"},
        )
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows
        result = correlation_matrix(db, dataset_id=1)

    corr_xy = result["matrix"][0]["correlations"]["y"]
    assert corr_xy == pytest.approx(-1.0, abs=0.001)


def test_correlation_insufficient_columns():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "analytics.statistics.get_column_types",
            lambda *a: {"x": "numeric"},
        )
        result = correlation_matrix(MagicMock(), dataset_id=1)

    assert result["matrix"] == []


def test_correlation_diagonal_is_one():
    class FakeRow:
        def __init__(self, a, b):
            self.a = float(a)
            self.b = float(b)

    rows = [FakeRow(i, i + 1) for i in range(1, 11)]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            "analytics.statistics.get_column_types",
            lambda *a: {"a": "numeric", "b": "numeric"},
        )
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows
        result = correlation_matrix(db, dataset_id=1)

    for row_entry in result["matrix"]:
        self_corr = row_entry["correlations"][row_entry["column"]]
        assert self_corr == pytest.approx(1.0, abs=0.001)


# ─── distribution_analysis ───────────────────────────────────────────────────


def test_distribution_bucket_count():
    values = list(range(100))
    db = _make_value_db(values)
    result = distribution_analysis(db, dataset_id=1, column="x", buckets=10)

    assert result["buckets"] == 10
    assert len(result["counts"]) == 10
    assert len(result["bin_edges"]) == 11
    assert len(result["bin_centres"]) == 10


def test_distribution_counts_sum_to_total():
    values = list(range(50))
    db = _make_value_db(values)
    result = distribution_analysis(db, dataset_id=1, column="x", buckets=5)
    assert sum(result["counts"]) == 50


def test_distribution_empty_returns_none():
    db = _make_value_db([])
    assert distribution_analysis(db, dataset_id=1, column="x") is None


# ─── time_series operations ──────────────────────────────────────────────────


def _ts_row(period, alias, value):
    return {"period": period, alias: value}


def test_moving_average_correct_values():
    ts_data = [
        {"period": f"2024-0{i+1}-01", "sum_revenue": float(i + 1)}
        for i in range(6)
    ]  # values: 1,2,3,4,5,6

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.time_series.resample_by_period", lambda *a, **kw: ts_data)
        result = moving_average(MagicMock(), 1, "joined", "revenue", window=3)

    # First two entries have no MA (not enough preceding values)
    assert result[0]["moving_avg"] is None
    assert result[1]["moving_avg"] is None
    # Third entry: MA of [1,2,3] = 2.0
    assert result[2]["moving_avg"] == pytest.approx(2.0)
    # Fourth entry: MA of [2,3,4] = 3.0
    assert result[3]["moving_avg"] == pytest.approx(3.0)


def test_moving_average_empty():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.time_series.resample_by_period", lambda *a, **kw: [])
        result = moving_average(MagicMock(), 1, "date", "value", window=7)
    assert result == []


def test_growth_rate_correct_pct():
    ts_data = [
        {"period": "2024-01-01", "sum_revenue": 100.0},
        {"period": "2024-02-01", "sum_revenue": 110.0},
        {"period": "2024-03-01", "sum_revenue": 99.0},
    ]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.time_series.resample_by_period", lambda *a, **kw: ts_data)
        result = growth_rate(MagicMock(), 1, "revenue", "joined", "month")

    assert result[0]["growth_rate_pct"] is None         # first period
    assert result[1]["growth_rate_pct"] == pytest.approx(10.0)   # +10%
    assert result[2]["growth_rate_pct"] == pytest.approx(-10.0)  # -10%


def test_growth_rate_zero_previous_returns_none():
    ts_data = [
        {"period": "2024-01-01", "sum_revenue": 0.0},
        {"period": "2024-02-01", "sum_revenue": 50.0},
    ]

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.time_series.resample_by_period", lambda *a, **kw: ts_data)
        result = growth_rate(MagicMock(), 1, "revenue", "joined")

    assert result[1]["growth_rate_pct"] is None


def test_growth_rate_empty():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("analytics.time_series.resample_by_period", lambda *a, **kw: [])
        result = growth_rate(MagicMock(), 1, "revenue", "date")
    assert result == []


# ─── export ──────────────────────────────────────────────────────────────────


def test_export_to_csv_basic():
    rows = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
    csv_out = export_to_csv(rows)
    lines = csv_out.strip().split("\n")
    assert lines[0] == "name,age"
    assert "Alice,30" in lines[1]
    assert "Bob,25" in lines[2]


def test_export_to_csv_empty():
    assert export_to_csv([]) == ""


def test_export_to_csv_special_characters():
    rows = [{"note": 'He said, "hello"'}]
    csv_out = export_to_csv(rows)
    assert '"He said, ""hello"""' in csv_out


def test_export_to_json_basic():
    rows = [{"name": "Alice", "score": 9.5}]
    json_out = export_to_json(rows)
    import json
    parsed = json.loads(json_out)
    assert parsed[0]["name"] == "Alice"
    assert parsed[0]["score"] == 9.5


def test_export_to_json_empty():
    import json
    assert json.loads(export_to_json([])) == []


def test_export_to_json_non_serializable_coerced():
    from datetime import date
    rows = [{"day": date(2024, 1, 15)}]
    json_out = export_to_json(rows)
    assert "2024-01-15" in json_out


def test_flatten_row():
    row = {"id": 42, "data": {"name": "Alice", "age": 30}}
    flat = flatten_row(row)
    assert flat["_row_id"] == 42
    assert flat["name"] == "Alice"
    assert flat["age"] == 30


def test_flatten_row_missing_data():
    flat = flatten_row({"id": 1, "data": {}})
    assert flat["_row_id"] == 1


def test_export_to_csv_column_order_stable():
    rows = [{"z": 3, "a": 1, "m": 2}]
    csv_out = export_to_csv(rows)
    header = csv_out.split("\n")[0]
    assert header == "z,a,m"  # preserves insertion order
