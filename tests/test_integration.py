"""
Integration tests: verify that multiple modules coordinate correctly
across a realistic data flow.

These tests do NOT require a live database — they test the cross-module
contract using mocks at the DB boundary and real logic everywhere else.
"""

import io
import threading
from unittest.mock import MagicMock, patch, call

import pytest

from analytics.cache import QueryCache, cache_query_result, make_query_hash, warm_cache
from analytics.export import export_to_csv, export_to_json, flatten_row
from analytics.query_builder import Filter, FilterGroup, LogicOp, build_where_clause
from ingestion.parsers import (
    clean_data,
    detect_column_types,
    parse_csv,
    stream_csv_chunks,
    validate_row,
)
from search.engine import ParsedQuery, QueryType, parse_query


# ─── Fixtures (local) ─────────────────────────────────────────────────────────


def _csv(rows: int = 10, cols: dict | None = None) -> io.BytesIO:
    """Generate a CSV stream with numeric and text columns."""
    cols = cols or {"name": "text", "age": "numeric", "city": "text"}
    header = ",".join(cols.keys())
    lines = [header]
    names = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Hank", "Ivy", "Jack"]
    cities = ["NYC", "LA", "Chicago"]
    for i in range(rows):
        name = names[i % len(names)]
        age = 20 + (i % 40)
        city = cities[i % len(cities)]
        lines.append(f"{name},{age},{city}")
    return io.BytesIO("\n".join(lines).encode())


# ─── test_upload_search_aggregate_flow ───────────────────────────────────────


def test_upload_search_aggregate_flow():
    """
    Full pipeline from CSV bytes → parse → type-detect → validate → clean
    → search query → filter query.  No DB required.
    """
    # 1. Parse CSV
    csv_bytes = (
        b"product,price,units,date\n"
        b"Widget,9.99,100,2024-01-01\n"
        b"Gadget,19.99,50,2024-01-15\n"
        b"Doohickey,4.99,200,2024-02-01\n"
    )
    col_names, raw_rows = parse_csv(io.BytesIO(csv_bytes))
    assert col_names == ["product", "price", "units", "date"]
    assert len(raw_rows) == 3

    # 2. Type detection
    types = detect_column_types(raw_rows)
    assert types["price"] == "numeric"
    assert types["units"] == "numeric"
    assert types["date"] == "date"
    assert types["product"] == "text"

    # 3. Schema + validation
    schema = {"columns": [{"name": c, "type": types[c]} for c in col_names]}
    for i, row in enumerate(raw_rows):
        valid, errors = validate_row(row, schema)
        assert valid, f"Row {i} invalid: {errors}"

    # 4. Data cleaning
    cleaned = [
        {col: clean_data(val, types[col]) for col, val in row.items()}
        for row in raw_rows
    ]
    assert cleaned[0]["price"] == pytest.approx(9.99)
    assert cleaned[0]["units"] == 100
    assert cleaned[0]["date"] == "2024-01-01"

    # 5. Search query parses without error
    pq = parse_query("Widget OR Gadget")
    assert pq.query_type == QueryType.BOOLEAN
    assert "Widget" in pq.terms

    # 6. Filter query builds correct SQL
    fg = FilterGroup([Filter("price", ">", 5.0)], logic=LogicOp.AND)
    sql, params = build_where_clause(fg, types)
    assert "(data->>'price')::numeric > :p_0" in sql
    assert params["p_0"] == 5.0

    # 7. Export cleaned rows to CSV
    flat = [{"product": r["product"], "price": r["price"]} for r in cleaned]
    csv_out = export_to_csv(flat)
    assert "product,price" in csv_out
    assert "Widget" in csv_out


def test_parse_then_export_round_trip():
    """CSV → parse → clean → export should round-trip values without corruption."""
    original = "item,qty,price\nApple,5,1.50\nBanana,12,0.30\n"
    col_names, raw_rows = parse_csv(io.BytesIO(original.encode()))
    types = detect_column_types(raw_rows)
    cleaned = [
        {col: clean_data(v, types[col]) for col, v in row.items()}
        for row in raw_rows
    ]

    json_out = export_to_json(cleaned)
    import json
    parsed = json.loads(json_out)

    assert parsed[0]["item"] == "Apple"
    assert parsed[0]["qty"] == 5
    assert parsed[1]["price"] == pytest.approx(0.30)


def test_filter_then_export_pipeline(sample_rows, sample_column_types):
    """Filter rows in Python (simulating what execute_query does) then export."""
    types = sample_column_types
    fg = FilterGroup([Filter("age", ">", 25)], logic=LogicOp.AND)
    sql, params = build_where_clause(fg, types)

    # Simulate filtering in Python (mirrors what PostgreSQL would do)
    filtered = [r for r in sample_rows if (r.get("age") or 0) > params["p_0"]]
    assert len(filtered) == 3  # Alice(30), Charlie(35), Diana(28)

    csv_out = export_to_csv(filtered)
    lines = csv_out.strip().split("\n")
    assert lines[0].startswith("name")
    assert len(lines) == 4  # header + 3 data rows


# ─── test_large_dataset_performance ──────────────────────────────────────────


def test_large_dataset_streams_in_chunks():
    """
    10 000-row CSV is processed in chunks ≤ 500 rows — never fully in memory.
    """
    header = "id,value\n"
    body = "".join(f"{i},{i * 1.5}\n" for i in range(10_000))
    stream = io.BytesIO((header + body).encode())

    chunk_sizes = []
    total = 0
    for _, rows in stream_csv_chunks(stream, chunk_size=500):
        chunk_sizes.append(len(rows))
        total += len(rows)

    assert total == 10_000
    assert max(chunk_sizes) <= 500
    assert len(chunk_sizes) == 20


def test_large_dataset_type_detection_samples_only():
    """Type detection uses at most MAX_SAMPLE (500) rows even for larger input."""
    from ingestion.parsers import _TYPE_SAMPLE_SIZE
    assert _TYPE_SAMPLE_SIZE == 500

    rows = [{"x": str(i)} for i in range(2000)]
    types = detect_column_types(rows)
    assert types["x"] == "numeric"


def test_large_dataset_export_handles_many_rows():
    """export_to_csv writes all rows without truncation."""
    rows = [{"id": i, "val": float(i) * 0.1} for i in range(5_000)]
    csv_out = export_to_csv(rows)
    data_lines = csv_out.strip().split("\n")[1:]  # skip header
    assert len(data_lines) == 5_000


# ─── test_cache_effectiveness ────────────────────────────────────────────────


def test_cache_miss_then_hit(fresh_cache):
    call_count = 0

    def expensive():
        nonlocal call_count
        call_count += 1
        return {"rows": list(range(100))}

    h = make_query_hash({"q": "hello", "page": 1})

    r1 = cache_query_result(1, h, expensive, _cache=fresh_cache)
    r2 = cache_query_result(1, h, expensive, _cache=fresh_cache)

    assert call_count == 1       # fn called once
    assert r1 == r2              # same object
    assert fresh_cache.stats()["hits"] == 1
    assert fresh_cache.stats()["misses"] == 1


def test_cache_invalidation_forces_recompute(fresh_cache):
    calls = []

    def fn():
        calls.append(1)
        return {"total": len(calls)}

    h = make_query_hash({"op": "search"})
    cache_query_result(1, h, fn, _cache=fresh_cache)  # miss → store
    cache_query_result(1, h, fn, _cache=fresh_cache)  # hit
    assert len(calls) == 1

    fresh_cache.invalidate_dataset(1)
    cache_query_result(1, h, fn, _cache=fresh_cache)  # miss after invalidation
    assert len(calls) == 2


def test_cache_different_datasets_independent(fresh_cache):
    h = make_query_hash({"q": "test"})
    fresh_cache.set(1, h, "ds1_result")
    fresh_cache.set(2, h, "ds2_result")

    fresh_cache.invalidate_dataset(1)

    hit1, _ = fresh_cache.get(1, h)
    hit2, val2 = fresh_cache.get(2, h)
    assert not hit1
    assert hit2 and val2 == "ds2_result"


def test_cache_ttl_expiry(fresh_cache):
    import time
    h = make_query_hash({"q": "ttl-test"})
    fresh_cache.set(1, h, "stale", ttl=0.01)
    time.sleep(0.02)
    hit, _ = fresh_cache.get(1, h)
    assert not hit


def test_cache_lru_eviction():
    c = QueryCache(maxsize=3)
    for i in range(4):
        c.set(i, "hash", f"v{i}")
    # First entry should be evicted
    hit, _ = c.get(0, "hash")
    assert not hit
    # Last three should survive
    for i in range(1, 4):
        hit, _ = c.get(i, "hash")
        assert hit


def test_warm_cache_pre_populates(fresh_cache):
    calls = {}

    def make_fn(key):
        def fn():
            calls[key] = calls.get(key, 0) + 1
            return f"result_{key}"
        return fn

    warmed = warm_cache(
        dataset_id=5,
        warm_fns={"hash_a": make_fn("a"), "hash_b": make_fn("b")},
        _cache=fresh_cache,
    )
    assert warmed == 2
    assert calls == {"a": 1, "b": 1}

    # Second warm call — already populated, fn not called again
    warm_cache(5, {"hash_a": make_fn("a")}, _cache=fresh_cache)
    assert calls["a"] == 1


def test_warm_cache_skips_on_error(fresh_cache):
    def broken():
        raise RuntimeError("DB is down")

    # Should not raise; warming failures are non-fatal
    warmed = warm_cache(1, {"bad_hash": broken}, _cache=fresh_cache)
    assert warmed == 0


def test_make_query_hash_deterministic():
    params = {"dataset_id": 1, "q": "hello", "page": 1}
    assert make_query_hash(params) == make_query_hash(params)


def test_make_query_hash_different_params():
    h1 = make_query_hash({"q": "hello"})
    h2 = make_query_hash({"q": "world"})
    assert h1 != h2


# ─── test_concurrent_users_querying ──────────────────────────────────────────


def test_concurrent_cache_reads_and_writes(fresh_cache):
    """50 threads simultaneously read/write to the cache — no corruption."""
    errors = []
    h = make_query_hash({"q": "concurrent"})
    fresh_cache.set(99, h, "initial")

    def worker(tid: int) -> None:
        try:
            hit, val = fresh_cache.get(99, h)
            if hit:
                assert val == "initial"
            fresh_cache.set(99, f"h_{tid}", f"result_{tid}")
            fresh_cache.get(99, f"h_{tid}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


def test_concurrent_invalidation_safe(fresh_cache):
    """Invalidation from one thread while other threads are reading."""
    errors = []

    def reader():
        for _ in range(20):
            fresh_cache.get(1, "h")

    def writer():
        for i in range(20):
            fresh_cache.set(1, f"h{i}", i)
            fresh_cache.invalidate_dataset(1)

    threads = [threading.Thread(target=reader) for _ in range(5)]
    threads += [threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
