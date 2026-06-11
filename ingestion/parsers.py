"""
CSV parsing, type detection, validation, and cleaning.
All functions are pure (no DB dependency) so they are easy to test in isolation.
"""

import csv as _csv
import io
from datetime import datetime
from typing import Any, Generator

import chardet
import pandas as pd

DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
]

_TYPE_SAMPLE_SIZE = 500
_TYPE_THRESHOLD = 0.80


# ─── Encoding ────────────────────────────────────────────────────────────────


def detect_encoding(raw: bytes, sample_size: int = 20_000) -> str:
    result = chardet.detect(raw[:sample_size])
    encoding = (result.get("encoding") or "utf-8").lower()
    if encoding in ("ascii", "iso-8859-1", "windows-1252"):
        return "latin-1"
    return encoding


def _decode(raw: bytes) -> str:
    encoding = detect_encoding(raw)
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("latin-1", errors="replace")


# ─── Parsing ─────────────────────────────────────────────────────────────────


def _normalise_columns(columns: list[str]) -> list[str]:
    """Strip whitespace and deduplicate by appending _N suffixes."""
    seen: dict[str, int] = {}
    result = []
    for col in columns:
        col = col.strip()
        if col in seen:
            seen[col] += 1
            result.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            result.append(col)
    return result


def _read_header(text: str) -> list[str]:
    """Extract and normalise the header row using Python's csv module."""
    reader = _csv.reader(io.StringIO(text), skipinitialspace=True)
    raw_header = next(reader, [])
    return _normalise_columns([c.strip() for c in raw_header])


def _df_to_rows(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame to a list of dicts with NaN → None."""
    return [
        {k: (None if pd.isna(v) else v) for k, v in record.items()}
        for record in df.to_dict(orient="records")
    ]


def parse_csv(file_stream: io.IOBase) -> tuple[list[str], list[dict]]:
    """
    Parse a full CSV (binary or text stream) into (column_names, rows).
    Rows are dicts of raw strings; None where the cell was empty.
    Uses pandas for robust quoting/multiline handling.
    """
    raw = file_stream.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    text = _decode(raw)
    column_names = _read_header(text)

    df = pd.read_csv(
        io.StringIO(text),
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        skipinitialspace=True,
        names=column_names,
        header=0,
    )

    return column_names, _df_to_rows(df)


def stream_csv_chunks(
    file_stream: io.IOBase,
    chunk_size: int = 500,
) -> Generator[tuple[list[str], list[dict]], None, None]:
    """
    Yield (column_names, rows) for each chunk.
    Lets large files be inserted without loading everything into memory.
    Requires a seekable stream (FastAPI UploadFile qualifies).
    """
    raw = file_stream.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    text = _decode(raw)
    reader = pd.read_csv(
        io.StringIO(text),
        dtype=str,
        keep_default_na=False,
        na_values=[""],
        skipinitialspace=True,
        chunksize=chunk_size,
    )

    columns: list[str] | None = None
    for chunk in reader:
        if columns is None:
            columns = _normalise_columns(list(chunk.columns))
        chunk.columns = columns
        yield columns, _df_to_rows(chunk)


# ─── Type detection ───────────────────────────────────────────────────────────


def _is_numeric(value: str) -> bool:
    try:
        float(value.replace(",", "").strip())
        return True
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    v = value.strip()
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(v, fmt)
            return True
        except ValueError:
            continue
    return False


def detect_column_types(rows: list[dict]) -> dict[str, str]:
    """
    Infer 'numeric' | 'text' | 'date' for every column by majority vote
    over up to _TYPE_SAMPLE_SIZE non-null values.
    """
    if not rows:
        return {}

    columns = list(rows[0].keys())
    result: dict[str, str] = {}

    for col in columns:
        sample = [r[col] for r in rows if r.get(col) is not None][:_TYPE_SAMPLE_SIZE]

        if not sample:
            result[col] = "text"
            continue

        n = len(sample)
        numeric_ratio = sum(_is_numeric(v) for v in sample) / n
        date_ratio = sum(_is_date(v) for v in sample) / n

        if numeric_ratio >= _TYPE_THRESHOLD:
            result[col] = "numeric"
        elif date_ratio >= _TYPE_THRESHOLD:
            result[col] = "date"
        else:
            result[col] = "text"

    return result


# ─── Cleaning ────────────────────────────────────────────────────────────────


def clean_data(value: Any, data_type: str) -> Any:
    """
    Convert a raw string to the correct Python type.
    Returns None for empty/unparseable values — never raises.
    """
    if value is None or str(value).strip() == "":
        return None

    if data_type == "numeric":
        try:
            f = float(str(value).replace(",", "").strip())
            return int(f) if f == int(f) else f
        except (ValueError, OverflowError):
            return None

    if data_type == "date":
        v = str(value).strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(v, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    return str(value).strip() or None


# ─── Validation ──────────────────────────────────────────────────────────────


def validate_row(row: dict, schema: dict) -> tuple[bool, list[str]]:
    """
    Validate one row against the dataset schema.
    schema: {"columns": [{"name": "age", "type": "numeric"}, ...]}
    Returns (is_valid, errors).
    """
    errors: list[str] = []
    columns = {c["name"]: c["type"] for c in schema.get("columns", [])}

    for col_name, data_type in columns.items():
        raw = row.get(col_name)
        if raw is None:
            continue
        cleaned = clean_data(raw, data_type)
        if cleaned is None and raw not in ("", None):
            errors.append(f"'{col_name}': cannot parse '{raw}' as {data_type}")

    return not errors, errors
