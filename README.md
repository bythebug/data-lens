# data-lens

An analytics and search platform for tabular datasets. Upload a CSV, then query, search, aggregate, and analyze it through a REST API — no SQL knowledge required.

---

## Features

| Capability | Details |
|---|---|
| **CSV ingestion** | Auto-detect column types (numeric / text / date), streaming insert |
| **Full-text search** | PostgreSQL FTS with stemming, phrase search, boolean operators |
| **Structured filtering** | `=` `!=` `>` `<` `>=` `<=` `IN` `LIKE` `IS NULL` with AND / OR |
| **Aggregation** | `GROUP BY` + `SUM` `AVG` `COUNT` `MIN` `MAX` `STDDEV` |
| **Time-series** | Resample by day/week/month, moving average, growth rate |
| **Statistics** | Mean, median, std dev, skewness, kurtosis, percentiles, outliers |
| **Correlation** | Pearson correlation matrix across all numeric columns |
| **Export** | CSV or JSON download |
| **Performance** | Query result cache (TTL + LRU), per-dataset partial GIN indexes |

---

## Quick start (Docker)

```bash
git clone https://github.com/bythebug/data-lens
cd data-lens
docker compose up --build
```

The API is available at `http://localhost:8000`. The PostgreSQL database is automatically initialised from `migrations/001_initial_schema.sql`.

---

## Upload a dataset

```bash
# Upload employees.csv and create dataset ID 1
curl -X POST http://localhost:8000/datasets \
  -H "X-User-Id: 1" \
  -F "name=employees" \
  -F "file=@employees.csv"
# → {"dataset_id": 1, "row_count": 5000}
```

> **Auth**: The current build uses a simple `X-User-Id` header. Replace `current_user_id` in `api/routes.py` with a real JWT/session check before production.

---

## API overview

### Search (full-text)

```bash
curl "http://localhost:8000/datasets/1/search?q=engineer" -H "X-User-Id: 1"
curl "http://localhost:8000/datasets/1/search?q=\"data+scientist\"" -H "X-User-Id: 1"
curl "http://localhost:8000/datasets/1/search?q=python+OR+java" -H "X-User-Id: 1"
```

### Filter (structured)

```bash
curl "http://localhost:8000/datasets/1/query?\
filters=%5B%7B%22column%22%3A%22age%22%2C%22operator%22%3A%22%3E%22%2C%22value%22%3A30%7D%5D\
&sort_by=salary&sort_dir=DESC" \
  -H "X-User-Id: 1"
```

### Aggregate

```bash
curl "http://localhost:8000/datasets/1/aggregate?\
group_by=department&metrics=SUM(salary),AVG(age),COUNT(*)" \
  -H "X-User-Id: 1"
```

### Statistics

```bash
curl "http://localhost:8000/datasets/1/stats/salary" -H "X-User-Id: 1"
curl "http://localhost:8000/datasets/1/correlations"  -H "X-User-Id: 1"
curl "http://localhost:8000/datasets/1/outliers/salary" -H "X-User-Id: 1"
```

### Export

```bash
curl "http://localhost:8000/datasets/1/export?format=csv" \
  -H "X-User-Id: 1" -o employees_export.csv
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        FastAPI                          │
│   api/routes.py — all HTTP endpoints + response models  │
└──────────────┬──────────────────────────────────────────┘
               │
    ┌──────────┴───────────────────────────┐
    │                                      │
    ▼                                      ▼
ingestion/                           analytics/
  parsers.py   ← chardet + pandas      query_builder.py  ← parameterised SQL
  pipeline.py  ← chunked DB insert     aggregation.py    ← GROUP BY + metrics
                                        statistics.py     ← numpy / scipy
search/                                 time_series.py    ← MA, growth rate
  fts.py       ← GIN index DDL          optimizer.py      ← EXPLAIN + slow log
  engine.py    ← websearch_to_tsquery   indexing_strategy.py ← index advisor
                                        cache.py          ← TTL + LRU cache
                                        export.py         ← CSV / JSON
               │
               ▼
          PostgreSQL 16
    dataset_rows  (JSONB data column)
    dataset_columns (type metadata)
    search_indexes  (index registry)
```

### Key design decisions

**JSONB rows** — Each CSV row is stored as `{"age": 30, "name": "Alice"}` in a single `data JSONB` column. No DDL is needed per upload; schema is tracked in `dataset_columns`.

**Partial expression indexes** — Each searchable column gets its own `WHERE dataset_id = N` partial index, keeping every index small and dataset-isolated.

**Hybrid SQL/numpy statistics** — PostgreSQL handles filtering and limiting (up to 50k rows sampled); numpy/scipy computes skewness, kurtosis, correlation, and IQR outlier fences.

**In-process cache** — A thread-safe TTL + LRU `QueryCache` reduces DB load for repeated queries. Per-dataset invalidation ensures freshness after new data is ingested.

---

## Project structure

```
data-lens/
├── main.py                  ← FastAPI app (uvicorn main:app)
├── config.py                ← DATABASE_URL env var
├── db/
│   ├── models.py            ← SQLAlchemy ORM models
│   └── session.py           ← engine + get_db() dependency
├── ingestion/
│   ├── parsers.py           ← CSV parsing, type detection, cleaning
│   └── pipeline.py          ← ingest_dataset (chunked, transactional)
├── search/
│   ├── fts.py               ← GIN index DDL + tsvector helpers
│   └── engine.py            ← parse_query, search_dataset
├── analytics/
│   ├── query_builder.py     ← Filter model + WHERE clause builder
│   ├── aggregation.py       ← Metric model + aggregate_dataset
│   ├── statistics.py        ← basic_stats, quantiles, correlation, outliers
│   ├── time_series.py       ← resample, moving_average, growth_rate
│   ├── optimizer.py         ← EXPLAIN ANALYZE, slow query logging
│   ├── indexing_strategy.py ← index recommendations + lifecycle
│   ├── cache.py             ← QueryCache, cache_query_result, warm_cache
│   ├── export.py            ← CSV / JSON export
│   └── stats.py             ← SQL-native column stats
├── api/
│   └── routes.py            ← all FastAPI endpoints
├── migrations/
│   └── 001_initial_schema.sql
├── tests/
│   ├── conftest.py          ← shared fixtures
│   ├── test_ingestion.py
│   ├── test_search.py
│   ├── test_filtering.py
│   ├── test_aggregation.py
│   ├── test_performance.py
│   ├── test_statistics.py
│   ├── test_integration.py
│   └── test_edge_cases.py
└── schema.sql               ← canonical DDL reference
```

---

## Running tests

```bash
# All tests
pytest

# With coverage
pytest --cov --cov-report=term-missing

# One file
pytest tests/test_statistics.py -v
```

267 tests, 76% coverage (API routes require a live DB for E2E coverage).

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://localhost/data_lens` | PostgreSQL connection string |
