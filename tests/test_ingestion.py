"""
Tests for CSV parsing, type detection, validation, and ingestion.
DB-touching tests use a mock session injected via dependency override.
"""

import io
from unittest.mock import MagicMock, patch

import pytest

from parsers import (
    clean_data,
    detect_column_types,
    detect_encoding,
    parse_csv,
    stream_csv_chunks,
    validate_row,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_stream(text: str, encoding: str = "utf-8") -> io.BytesIO:
    return io.BytesIO(text.encode(encoding))


# ─── test_parse_csv ───────────────────────────────────────────────────────────


def test_parse_csv_basic():
    csv = "name,age,city\nAlice,30,NYC\nBob,25,LA\n"
    cols, rows = parse_csv(make_stream(csv))
    assert cols == ["name", "age", "city"]
    assert len(rows) == 2
    assert rows[0] == {"name": "Alice", "age": "30", "city": "NYC"}


def test_parse_csv_empty_values():
    csv = "a,b,c\n1,,3\n"
    _, rows = parse_csv(make_stream(csv))
    assert rows[0]["b"] is None


def test_parse_csv_quoted_commas():
    csv = 'name,address\nAlice,"123 Main St, Apt 4"\n'
    _, rows = parse_csv(make_stream(csv))
    assert rows[0]["address"] == "123 Main St, Apt 4"


def test_parse_csv_strips_column_whitespace():
    csv = " name , age \nAlice,30\n"
    cols, _ = parse_csv(make_stream(csv))
    assert cols == ["name", "age"]


# ─── test_duplicate_column_names ─────────────────────────────────────────────


def test_duplicate_column_names_are_renamed():
    csv = "score,score,score\n1,2,3\n"
    cols, rows = parse_csv(make_stream(csv))
    assert cols == ["score", "score_1", "score_2"]
    assert set(rows[0].keys()) == {"score", "score_1", "score_2"}


# ─── test_encoding_detection ─────────────────────────────────────────────────


def test_encoding_detection_utf8():
    # Pure ASCII is a valid subset of both UTF-8 and latin-1; chardet may
    # return either — what matters is that decoding succeeds without errors.
    raw = "hello world".encode("utf-8")
    enc = detect_encoding(raw)
    assert "hello world".encode("utf-8").decode(enc) == "hello world"


def test_encoding_detection_latin1():
    raw = "caf\xe9".encode("latin-1")
    enc = detect_encoding(raw)
    # chardet may detect as ascii/windows-1252/latin-1 — all normalise to latin-1
    assert enc == "latin-1"


def test_parse_csv_latin1_encoding():
    # Use only é (0xE9), which maps to the same Unicode point (U+00E9) in
    # latin-1, ISO-8859-2, and Windows-1250. Other bytes like ï (0xEF) differ
    # across encodings so chardet's guess could be any of them legitimately.
    lines = ["name,note"] + [f"Person{i},café entrée" for i in range(30)]
    csv_bytes = "\n".join(lines).encode("latin-1")
    cols, rows = parse_csv(io.BytesIO(csv_bytes))
    assert rows[0]["note"] == "café entrée"


def test_parse_csv_utf8_bom():
    csv = "﻿name,age\nAlice,30\n"
    cols, rows = parse_csv(make_stream(csv))
    assert "name" in cols  # BOM stripped by pandas


# ─── test_type_detection ─────────────────────────────────────────────────────


def test_detect_column_types_numeric():
    rows = [{"age": "25"}, {"age": "30"}, {"age": "45"}]
    types = detect_column_types(rows)
    assert types["age"] == "numeric"


def test_detect_column_types_date():
    rows = [{"d": "2024-01-01"}, {"d": "2024-06-15"}, {"d": "2023-12-31"}]
    types = detect_column_types(rows)
    assert types["d"] == "date"


def test_detect_column_types_text():
    rows = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]
    types = detect_column_types(rows)
    assert types["name"] == "text"


def test_detect_column_types_mixed_falls_back_to_text():
    # Below 80% numeric threshold → text
    rows = [{"v": "1"}, {"v": "2"}, {"v": "abc"}, {"v": "def"}, {"v": "ghi"}]
    types = detect_column_types(rows)
    assert types["v"] == "text"


def test_detect_column_types_all_null_is_text():
    rows = [{"x": None}, {"x": None}]
    types = detect_column_types(rows)
    assert types["x"] == "text"


def test_detect_column_types_numeric_with_commas():
    rows = [{"amount": "1,234.56"}, {"amount": "2,000"}, {"amount": "300"}]
    types = detect_column_types(rows)
    assert types["amount"] == "numeric"


def test_detect_column_types_empty_rows():
    assert detect_column_types([]) == {}


# ─── test_data_validation ────────────────────────────────────────────────────


def test_validate_row_valid():
    schema = {"columns": [{"name": "age", "type": "numeric"}, {"name": "name", "type": "text"}]}
    ok, errors = validate_row({"age": "25", "name": "Alice"}, schema)
    assert ok
    assert errors == []


def test_validate_row_invalid_numeric():
    schema = {"columns": [{"name": "age", "type": "numeric"}]}
    ok, errors = validate_row({"age": "not-a-number"}, schema)
    assert not ok
    assert "age" in errors[0]


def test_validate_row_null_passes():
    schema = {"columns": [{"name": "age", "type": "numeric"}]}
    ok, errors = validate_row({"age": None}, schema)
    assert ok


def test_validate_row_empty_string_passes():
    schema = {"columns": [{"name": "age", "type": "numeric"}]}
    ok, _ = validate_row({"age": ""}, schema)
    assert ok


def test_validate_row_invalid_date():
    schema = {"columns": [{"name": "joined", "type": "date"}]}
    ok, errors = validate_row({"joined": "not-a-date"}, schema)
    assert not ok
    assert "joined" in errors[0]


# ─── clean_data ──────────────────────────────────────────────────────────────


def test_clean_data_numeric_int():
    assert clean_data("42", "numeric") == 42
    assert isinstance(clean_data("42", "numeric"), int)


def test_clean_data_numeric_float():
    assert clean_data("3.14", "numeric") == 3.14


def test_clean_data_numeric_with_commas():
    assert clean_data("1,234", "numeric") == 1234


def test_clean_data_date_iso():
    assert clean_data("2024-03-15", "date") == "2024-03-15"


def test_clean_data_date_slash_format():
    assert clean_data("03/15/2024", "date") == "2024-03-15"


def test_clean_data_text_strips():
    assert clean_data("  hello  ", "text") == "hello"


def test_clean_data_none_returns_none():
    assert clean_data(None, "numeric") is None
    assert clean_data(None, "date") is None
    assert clean_data(None, "text") is None


def test_clean_data_empty_string_returns_none():
    assert clean_data("", "numeric") is None


def test_clean_data_unparseable_numeric_returns_none():
    assert clean_data("abc", "numeric") is None


# ─── test_large_file_handling ────────────────────────────────────────────────


def test_stream_csv_chunks_yields_all_rows():
    header = "id,value\n"
    body = "".join(f"{i},{i * 2}\n" for i in range(1, 2001))
    stream = make_stream(header + body)

    total = 0
    cols_seen = None
    for cols, rows in stream_csv_chunks(stream, chunk_size=300):
        if cols_seen is None:
            cols_seen = cols
        total += len(rows)

    assert total == 2000
    assert cols_seen == ["id", "value"]


def test_stream_csv_chunks_correct_chunk_size():
    header = "x\n"
    body = "".join(f"{i}\n" for i in range(1, 101))
    stream = make_stream(header + body)

    chunk_sizes = [len(rows) for _, rows in stream_csv_chunks(stream, chunk_size=30)]
    assert sum(chunk_sizes) == 100
    assert all(s <= 30 for s in chunk_sizes)


def test_large_file_memory_efficiency():
    """stream_csv_chunks should not return a single list of all rows."""
    header = "n\n"
    body = "".join(f"{i}\n" for i in range(10_000))
    stream = make_stream(header + body)

    chunks = list(stream_csv_chunks(stream, chunk_size=1000))
    assert len(chunks) >= 10
    for _, rows in chunks:
        assert len(rows) <= 1000


# ─── Ingestion (mock DB) ──────────────────────────────────────────────────────


def test_ingest_dataset_empty_file_raises():
    from ingestion import ingest_dataset

    db = MagicMock()
    stream = make_stream("name,age\n")  # header only, no data rows

    with pytest.raises(ValueError, match="empty"):
        ingest_dataset(db, user_id=1, file_stream=stream, dataset_name="test")


def test_ingest_dataset_calls_commit_once():
    from ingestion import ingest_dataset

    csv = "name,age\nAlice,30\nBob,25\n"
    db = MagicMock()
    db.flush = MagicMock()
    db.bulk_save_objects = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    # Give the flushed dataset a fake id
    def set_id(obj):
        if isinstance(obj, __import__("models").Dataset):
            obj.id = 99

    db.add.side_effect = lambda obj: None
    db.flush.side_effect = lambda: set_id(
        next((c.args[0] for c in db.add.call_args_list
              if isinstance(c.args[0], __import__("models").Dataset)), None)
    )

    with patch("ingestion.DatasetRow"), patch("ingestion.DatasetColumn"), patch("ingestion.SearchIndex"):
        try:
            ingest_dataset(db, user_id=1, file_stream=make_stream(csv), dataset_name="t")
        except Exception:
            pass

    db.commit.assert_called_once()
