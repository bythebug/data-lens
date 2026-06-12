"""
Shared pytest fixtures for data-lens tests.

Fixture scopes:
- function (default): recreated for every test — safe for mutable state.
- module: shared across a test file — use for read-only expensive setup.
- session: shared across the entire test run — use for immutable globals.
"""

import io
from unittest.mock import MagicMock

import pytest

from analytics.cache import QueryCache
from analytics.query_builder import Filter, FilterGroup, LogicOp
from ingestion.parsers import clean_data, detect_column_types, parse_csv


# ─── CSV fixtures ─────────────────────────────────────────────────────────────


SAMPLE_CSV = (
    "name,age,city,score,joined\n"
    "Alice,30,NYC,95.5,2022-03-15\n"
    "Bob,25,LA,87.0,2021-07-01\n"
    "Charlie,35,Chicago,72.3,2023-01-10\n"
    "Diana,28,NYC,91.0,2022-11-20\n"
    "Eve,22,LA,88.5,2023-06-05\n"
)

MINIMAL_CSV = "value\n1\n2\n3\n"

NUMERIC_ONLY_CSV = "x,y\n1,2\n3,4\n5,6\n7,8\n"


@pytest.fixture
def sample_csv_stream():
    """BytesIO stream of a 5-row mixed-type CSV."""
    return io.BytesIO(SAMPLE_CSV.encode())


@pytest.fixture
def minimal_csv_stream():
    """BytesIO stream of a single-column, 3-row CSV."""
    return io.BytesIO(MINIMAL_CSV.encode())


@pytest.fixture
def numeric_csv_stream():
    """BytesIO stream of a two-column numeric CSV."""
    return io.BytesIO(NUMERIC_ONLY_CSV.encode())


# ─── Parsed data fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sample_parsed():
    """
    Parsed column names, raw rows, inferred types, and cleaned rows
    for SAMPLE_CSV. Module-scoped because parsing is deterministic.
    """
    stream = io.BytesIO(SAMPLE_CSV.encode())
    col_names, raw_rows = parse_csv(stream)
    col_types = detect_column_types(raw_rows)
    cleaned = [
        {col: clean_data(val, col_types.get(col, "text")) for col, val in row.items()}
        for row in raw_rows
    ]
    return {"columns": col_names, "raw_rows": raw_rows, "types": col_types, "cleaned": cleaned}


@pytest.fixture
def sample_column_types():
    return {"name": "text", "age": "numeric", "city": "text", "score": "numeric", "joined": "date"}


@pytest.fixture
def sample_rows():
    """Cleaned row dicts matching SAMPLE_CSV."""
    return [
        {"name": "Alice",   "age": 30, "city": "NYC",     "score": 95.5, "joined": "2022-03-15"},
        {"name": "Bob",     "age": 25, "city": "LA",      "score": 87.0, "joined": "2021-07-01"},
        {"name": "Charlie", "age": 35, "city": "Chicago", "score": 72.3, "joined": "2023-01-10"},
        {"name": "Diana",   "age": 28, "city": "NYC",     "score": 91.0, "joined": "2022-11-20"},
        {"name": "Eve",     "age": 22, "city": "LA",      "score": 88.5, "joined": "2023-06-05"},
    ]


# ─── Database mock fixtures ───────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    """Fresh MagicMock Session for each test."""
    return MagicMock()


@pytest.fixture
def mock_db_with_rows(sample_rows):
    """Mock DB that returns sample_rows from execute().fetchall()."""
    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [
        type("Row", (), {"id": i + 1, "data": r})()
        for i, r in enumerate(sample_rows)
    ]
    db.execute.return_value = result
    return db


# ─── Filter fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def age_gt_filter():
    return FilterGroup([Filter("age", ">", 25)], logic=LogicOp.AND)


@pytest.fixture
def city_in_filter():
    return FilterGroup([Filter("city", "IN", ["NYC", "LA"])], logic=LogicOp.AND)


@pytest.fixture
def multi_filter():
    return FilterGroup(
        [Filter("age", ">=", 25), Filter("city", "=", "NYC")],
        logic=LogicOp.AND,
    )


# ─── Cache fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_cache():
    """Isolated QueryCache instance — never shares state with the module singleton."""
    return QueryCache(maxsize=32, default_ttl=60.0)


# ─── Parametrize helpers ──────────────────────────────────────────────────────

# Used by test_edge_cases.py parametrized tests
NUMERIC_EDGE_VALUES = [
    ("0",           0),
    ("-1",         -1),
    ("1e3",      1000),
    ("1.5e-2",   0.015),
    ("1,000",    1000),
    ("9999999", 9999999),
    ("-0.001", -0.001),
]

DATE_FORMATS_SAMPLE = [
    ("2024-01-15",   "2024-01-15"),
    ("01/15/2024",   "2024-01-15"),
    ("15/01/2024",   "2024-01-15"),
    ("2024/01/15",   "2024-01-15"),
]

SPECIAL_CHARS = [
    "café résumé",
    "naïve",
    "Hello World",          # space
    "O'Brien",              # single quote
    "100%",                 # percent
    "price: $9.99",         # dollar
    "<html>",               # angle brackets
    "tab\there",            # tab inside value
]
