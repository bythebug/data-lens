# data-lens Query Guide

Practical patterns for getting the most out of data-lens.

---

## 1. Filter syntax

Filters are a JSON array passed as the `filters` query parameter.
URL-encode it or wrap the entire URL in single quotes when using curl.

```bash
# Shorthand used in examples below — real requests need URL-encoding
FILTERS='[{"column":"age","operator":">","value":30}]'
curl "http://localhost:8000/datasets/1/query?filters=$FILTERS" -H "X-User-Id: 1"
```

### All operators

```json
[
  {"column": "age",    "operator": ">",          "value": 30},
  {"column": "age",    "operator": ">=",         "value": 18},
  {"column": "city",   "operator": "=",          "value": "NYC"},
  {"column": "city",   "operator": "!=",         "value": "LA"},
  {"column": "name",   "operator": "LIKE",       "value": "Ali%"},
  {"column": "name",   "operator": "ILIKE",      "value": "%alice%"},
  {"column": "region", "operator": "IN",         "value": ["East", "West"]},
  {"column": "bio",    "operator": "IS NULL"},
  {"column": "phone",  "operator": "IS NOT NULL"}
]
```

### Combining filters with AND / OR

```bash
# Employees in NYC aged over 30
FILTERS='[{"column":"city","operator":"=","value":"NYC"},
          {"column":"age","operator":">","value":30}]'
curl "…/query?filters=$FILTERS&logic=AND"

# Customers from NYC or LA
FILTERS='[{"column":"city","operator":"=","value":"NYC"},
          {"column":"city","operator":"=","value":"LA"}]'
curl "…/query?filters=$FILTERS&logic=OR"
```

---

## 2. Common use cases

### Cohort analysis

Segment customers by signup quarter and compute key metrics.

```bash
# Step 1 — Aggregate signups by quarter
curl "…/datasets/1/aggregate?\
group_by=plan_type\
&metrics=COUNT(*),AVG(revenue),SUM(revenue)\
&filters=[{\"column\":\"signup_date\",\"operator\":\">=\",\"value\":\"2024-01-01\"}]\
&sort_by=sum_revenue&sort_dir=DESC" \
  -H "X-User-Id: 1"
```

Expected output:
```json
{
  "rows": [
    {"plan_type": "enterprise", "count_all": 120, "avg_revenue": 5200.0, "sum_revenue": 624000.0},
    {"plan_type": "pro",        "count_all": 850, "avg_revenue": 299.0,  "sum_revenue": 254150.0},
    {"plan_type": "free",       "count_all": 4200,"avg_revenue": 0.0,    "sum_revenue": 0.0}
  ]
}
```

### Time-series analysis

Monthly revenue trend with moving average.

```bash
# Step 1 — Monthly revenue
curl "…/datasets/1/aggregate?\
time_column=order_date&time_truncate=month&metrics=SUM(revenue)" \
  -H "X-User-Id: 1"

# Step 2 — 3-month moving average (via time_series endpoint once implemented)
# For now: export the monthly data and compute the MA locally.
curl "…/datasets/1/export?format=json\
&filters=[{\"column\":\"order_date\",\"operator\":\">=\",\"value\":\"2023-01-01\"}]" \
  -H "X-User-Id: 1"
```

### Top-N analysis

Top 10 products by total revenue.

```bash
curl "…/datasets/1/aggregate?\
group_by=product_name\
&metrics=SUM(revenue),COUNT(*)\
&sort_by=sum_revenue&sort_dir=DESC" \
  -H "X-User-Id: 1"
# Returns up to 500 rows — take first 10 in your application layer.
```

### Outlier investigation

Find anomalous transactions, then inspect them.

```bash
# Step 1 — Get IQR fences for transaction amount
curl "…/datasets/1/outliers/amount" -H "X-User-Id: 1"
# Response: {"lower_fence": -500, "upper_fence": 8500, "outlier_count": 23, ...}

# Step 2 — Filter to outlier rows and export for manual review
FILTERS='[{"column":"amount","operator":">","value":8500}]'
curl "…/datasets/1/export?format=csv&filters=$FILTERS" \
  -H "X-User-Id: 1" -o outliers.csv
```

### Null analysis

Find columns with high null rates; identify affected rows.

```bash
# Step 1 — Count nulls per column
curl "…/datasets/1/stats" -H "X-User-Id: 1"
# Look at null_count for each column

# Step 2 — Export rows where email is null
FILTERS='[{"column":"email","operator":"IS NULL"}]'
curl "…/datasets/1/export?format=csv&filters=$FILTERS" \
  -H "X-User-Id: 1" -o missing_emails.csv
```

### Distribution comparison

Understand how a numeric column is distributed before choosing a model.

```bash
# 20-bucket histogram of salary
curl "…/datasets/1/distribution/salary?buckets=20" -H "X-User-Id: 1"

# Check skewness — high positive skew suggests log transformation before modelling
curl "…/datasets/1/stats/salary" -H "X-User-Id: 1"
# If skewness > 1.0 → consider log(salary) for regression
```

### Correlation exploration

Which numeric features correlate with the target variable?

```bash
curl "…/datasets/1/correlations" -H "X-User-Id: 1"
# Sort the matrix by |correlation_with_target| to find predictive features
```

---

## 3. Full-text search patterns

### Building effective search queries

| Intent | Query |
|---|---|
| Find rows mentioning "Python" | `python` |
| Find "data scientist" as a phrase | `"data scientist"` |
| Either Python or R skills | `python OR r` |
| Python skills, not management | `python -manager` |
| Senior engineer in any form | `senior engineer` |
| Exact title | `"senior software engineer"` |

### Combining FTS with filters

The `/search` endpoint supports `columns` to limit which text columns are searched.
Use `/query` + `ILIKE` when you need to combine text matching with numeric conditions
(FTS and structured filters are separate endpoints).

```bash
# Search in job_title column only
curl "…/datasets/1/search?q=engineer&columns=job_title" -H "X-User-Id: 1"

# High earners with "senior" in their title — two requests, intersect in app
curl "…/datasets/1/search?q=senior&columns=job_title" -H "X-User-Id: 1"
curl "…/datasets/1/query?filters=[{\"column\":\"salary\",\"operator\":\">=\",\"value\":120000}]" \
  -H "X-User-Id: 1"
```

---

## 4. Query optimisation tips

### Create indexes on frequently filtered columns

```bash
# The indexing_strategy module analyses your columns automatically.
# To trigger it programmatically (Python):
from analytics.indexing_strategy import suggest_indexes, create_expression_index
recs = suggest_indexes(db, dataset_id=7)
for rec in recs:
    create_expression_index(db, rec.dataset_id, rec.column_name, col_type, rec.index_type)
```

### Build FTS indexes on text columns before searching

```python
from search.fts import create_fts_index
create_fts_index(db, dataset_id=7, column_name="description")
```

### Use pagination

Always pass `page` and `page_size`. Without pagination, a filter that matches
all rows will return up to the default page size (20), but the `total` field
tells you how many rows exist.

### Filter before aggregating

Use the `filters` parameter on `/aggregate` to pre-filter rows before the
GROUP BY — this is far cheaper than aggregating everything and filtering the results.

```bash
# GOOD — PostgreSQL filters before grouping
curl "…/aggregate?group_by=region&metrics=SUM(revenue)\
&filters=[{\"column\":\"status\",\"operator\":\"=\",\"value\":\"active\"}]"

# AVOID — fetches all groups, then filter in app
curl "…/aggregate?group_by=region&metrics=SUM(revenue)"
# ... then filter response in code
```

### Use the cache

Repeated identical queries are served from the in-process TTL cache (default
TTL: 300s for stats, 60s for search). The cache is keyed by `(dataset_id, sha256(params))`.

To warm the cache for a dataset after ingestion:

```python
from analytics.cache import warm_cache, make_query_hash
from analytics.statistics import basic_stats

warm_cache(
    dataset_id=7,
    warm_fns={
        make_query_hash({"col": "revenue"}): lambda: basic_stats(db, 7, "revenue"),
    }
)
```

### Interpret EXPLAIN ANALYZE

```python
from analytics.optimizer import analyze_query

plan = analyze_query(db, "SELECT id, data FROM dataset_rows WHERE dataset_id = 7 LIMIT 100")
print(plan.seq_scans)    # tables with sequential scans
print(plan.suggestions)  # recommended indexes
```

If `seq_scans` contains `dataset_rows`, create an expression index on the
filtered column using `create_expression_index()`.
