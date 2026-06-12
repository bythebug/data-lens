"""
Query performance analysis: EXPLAIN ANALYZE, slow query logging, plan caching.
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SLOW_QUERY_THRESHOLD_S = 1.0  # log WARNING for queries that exceed this


# ─── Plan cache (in-process LRU + TTL) ───────────────────────────────────────


class _PlanCache:
    """
    Thread-safe LRU cache for EXPLAIN plans keyed by SQL string.
    Avoids running EXPLAIN ANALYZE on every request in production.
    """

    def __init__(self, maxsize: int = 128, ttl_seconds: float = 300.0) -> None:
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl_seconds

    def get(self, key: str) -> dict | None:
        if key not in self._store:
            return None
        value, ts = self._store[key]
        if time.monotonic() - ts > self.ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: dict) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.monotonic())
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()


plan_cache = _PlanCache()


# ─── Data models ─────────────────────────────────────────────────────────────


@dataclass
class QueryPlan:
    sql: str
    execution_time_ms: float
    planning_time_ms: float
    rows_estimated: int
    seq_scans: list[str]        # tables hit by sequential scans
    index_scans: list[str]      # tables hit by index scans
    suggestions: list[str]      # human-readable index recommendations
    raw_plan: dict = field(default_factory=dict, repr=False)


# ─── Timed execute ────────────────────────────────────────────────────────────


def timed_execute(
    db: Session,
    stmt: Any,
    params: dict[str, Any],
    label: str = "",
) -> Any:
    """
    Execute a SQLAlchemy statement, measure wall-clock time, and log slow queries.
    Returns the raw result proxy (caller calls .scalar(), .fetchall(), etc.).
    """
    start = time.perf_counter()
    result = db.execute(stmt, params)
    elapsed = time.perf_counter() - start

    tag = f" [{label}]" if label else ""
    if elapsed >= SLOW_QUERY_THRESHOLD_S:
        logger.warning("Slow query%s: %.3fs", tag, elapsed)
    else:
        logger.debug("Query%s: %.3fs", tag, elapsed)

    return result


# ─── EXPLAIN ANALYZE ─────────────────────────────────────────────────────────


def _walk_plan(node: dict, seq_scans: list[str], index_scans: list[str]) -> None:
    """Recursively walk the EXPLAIN JSON tree, collecting scan types."""
    node_type = node.get("Node Type", "")
    relation = node.get("Relation Name", "")

    if "Seq Scan" in node_type and relation:
        seq_scans.append(relation)
    elif "Index" in node_type and relation:
        index_scans.append(relation)

    for child in node.get("Plans", []):
        _walk_plan(child, seq_scans, index_scans)


def _make_suggestions(seq_scans: list[str]) -> list[str]:
    suggestions = []
    for table in sorted(set(seq_scans)):
        suggestions.append(
            f"'{table}' uses Seq Scan — add an expression index on the filtered "
            f"JSONB key, e.g.: CREATE INDEX ON {table} USING btree ((data->>'col'));"
        )
    return suggestions


def analyze_query(
    db: Session,
    sql: str,
    params: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> QueryPlan:
    """
    Run EXPLAIN (ANALYZE, FORMAT JSON) and return a structured QueryPlan.

    Caches results by SQL string to avoid re-running EXPLAIN on hot paths.
    WARNING: runs the query for real — do not call on write statements.
    """
    params = params or {}
    cache_key = sql  # params intentionally excluded (same structure, different values)

    if use_cache:
        cached = plan_cache.get(cache_key)
        if cached:
            return QueryPlan(**cached)

    row = db.execute(
        text(f"EXPLAIN (ANALYZE, FORMAT JSON, BUFFERS) {sql}"),
        params,
    ).fetchone()

    # PostgreSQL returns a JSON array with one element
    plan_data: dict = row[0][0]
    plan_node: dict = plan_data.get("Plan", {})

    seq_scans: list[str] = []
    index_scans: list[str] = []
    _walk_plan(plan_node, seq_scans, index_scans)

    result = QueryPlan(
        sql=sql,
        execution_time_ms=plan_data.get("Execution Time", 0.0),
        planning_time_ms=plan_data.get("Planning Time", 0.0),
        rows_estimated=plan_node.get("Plan Rows", 0),
        seq_scans=seq_scans,
        index_scans=index_scans,
        suggestions=_make_suggestions(seq_scans),
        raw_plan=plan_data,
    )

    if use_cache:
        plan_cache.set(cache_key, {
            "sql": result.sql,
            "execution_time_ms": result.execution_time_ms,
            "planning_time_ms": result.planning_time_ms,
            "rows_estimated": result.rows_estimated,
            "seq_scans": result.seq_scans,
            "index_scans": result.index_scans,
            "suggestions": result.suggestions,
            "raw_plan": result.raw_plan,
        })

    return result


def estimate_query_time(
    db: Session,
    sql: str,
    params: dict[str, Any] | None = None,
) -> float:
    """
    Run EXPLAIN (ANALYZE, FORMAT JSON) and return execution_time_ms only.
    Lighter than analyze_query when you only need the timing number.
    """
    params = params or {}
    row = db.execute(
        text(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}"),
        params,
    ).fetchone()
    return float(row[0][0].get("Execution Time", 0.0))
