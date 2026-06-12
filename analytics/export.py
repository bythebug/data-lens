"""
Export query results to CSV or JSON.
Pure functions — no DB dependency — so they are easy to test and reuse.
"""

import csv
import io
import json
from typing import Any


def export_to_csv(rows: list[dict[str, Any]]) -> str:
    """
    Serialise a list of flat dicts to a CSV string.
    Column order follows the key order of the first row.
    Returns an empty string for an empty list.
    """
    if not rows:
        return ""

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=list(rows[0].keys()),
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def export_to_json(rows: list[dict[str, Any]], indent: int = 2) -> str:
    """
    Serialise rows to a JSON string.
    Non-JSON-serialisable types (e.g. date, Decimal) are coerced to str.
    """
    return json.dumps(rows, indent=indent, default=str, ensure_ascii=False)


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten a {id, data} row (as returned by execute_query) into a single dict
    suitable for CSV/JSON export.  The `id` field is kept as `_row_id`.
    """
    flat: dict[str, Any] = {"_row_id": row.get("id")}
    flat.update(row.get("data") or {})
    return flat
