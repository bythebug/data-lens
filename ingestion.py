"""
Dataset ingestion: parse a CSV stream and persist it to the database.
Rows are inserted in chunks; the whole operation is one transaction.
"""

import io

from sqlalchemy.orm import Session

from models import Dataset, DatasetColumn, DatasetRow, SearchIndex
from parsers import (
    clean_data,
    detect_column_types,
    stream_csv_chunks,
    validate_row,
)

_CHUNK_SIZE = 500
# Number of rows sampled for type detection (first chunk is reused)
_SAMPLE_ROWS = 1000
# Number of rows validated before bulk insert begins
_VALIDATE_SAMPLE = 100


def _build_schema(column_names: list[str], column_types: dict[str, str]) -> dict:
    return {
        "columns": [
            {"name": name, "type": column_types.get(name, "text")}
            for name in column_names
        ]
    }


def _clean_row(raw: dict, column_types: dict[str, str]) -> dict:
    return {col: clean_data(val, column_types.get(col, "text")) for col, val in raw.items()}


def ingest_dataset(
    db: Session,
    user_id: int,
    file_stream: io.IOBase,
    dataset_name: str,
) -> Dataset:
    """
    Parse, validate, and store a CSV dataset in a single transaction.
    Streams rows in chunks of _CHUNK_SIZE to avoid loading the full file into memory.
    Raises ValueError for empty files or validation failures in the first sample.
    """
    column_names: list[str] | None = None
    column_types: dict[str, str] = {}
    schema: dict = {}
    dataset: Dataset | None = None
    total_rows = 0
    sample_buffer: list[dict] = []

    for chunk_cols, chunk_rows in stream_csv_chunks(file_stream, chunk_size=_CHUNK_SIZE):
        if not chunk_rows:
            continue

        # ── First chunk: type detection + schema creation ────────────────────
        if column_names is None:
            if not chunk_rows:
                raise ValueError("CSV file is empty or has no data rows")

            column_names = chunk_cols
            sample_buffer = chunk_rows[: _SAMPLE_ROWS]
            column_types = detect_column_types(sample_buffer)
            schema = _build_schema(column_names, column_types)

            # Validate first N rows early — surface obvious type mismatches
            for i, row in enumerate(chunk_rows[:_VALIDATE_SAMPLE]):
                valid, errors = validate_row(row, schema)
                if not valid:
                    raise ValueError(f"Row {i + 1} failed validation: {'; '.join(errors)}")

            dataset = Dataset(
                user_id=user_id,
                name=dataset_name,
                schema=schema,
                row_count=0,
            )
            db.add(dataset)
            db.flush()  # materialise dataset.id before child rows

            for col_name in column_names:
                db.add(DatasetColumn(
                    dataset_id=dataset.id,
                    column_name=col_name,
                    data_type=column_types.get(col_name, "text"),
                ))

            # Register a GIN-backed search index entry for the full document
            db.add(SearchIndex(
                dataset_id=dataset.id,
                column_name="*",
                index_type="btree",
            ))

        # ── Every chunk: clean + bulk insert ────────────────────────────────
        cleaned = [_clean_row(r, column_types) for r in chunk_rows]
        db.bulk_save_objects([
            DatasetRow(dataset_id=dataset.id, data=row)
            for row in cleaned
        ])
        total_rows += len(cleaned)

    if dataset is None:
        raise ValueError("CSV file is empty or has no data rows")

    dataset.row_count = total_rows
    db.commit()
    db.refresh(dataset)
    return dataset
