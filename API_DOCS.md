# data-lens API Reference

Base URL: `http://localhost:8000`  
Auth header: `X-User-Id: <integer>` on every request.

---

## Datasets

### Upload a CSV

```
POST /datasets
Content-Type: multipart/form-data
```

| Field | Type | Description |
|---|---|---|
| `file` | file | CSV file |
| `name` | string | Human-readable dataset name |

```bash
curl -X POST http://localhost:8000/datasets \
  -H "X-User-Id: 1" \
  -F "name=sales_2024" \
  -F "file=@sales.csv"
```

**Response 201**
```json
{"dataset_id": 7, "row_count": 12500}
```

Column types are auto-detected: values ≥ 80% parseable as a number → `numeric`; as a date → `date`; otherwise → `text`.

---

### List datasets

```
GET /datasets
```

```bash
curl http://localhost:8000/datasets -H "X-User-Id: 1"
```

**Response 200**
```json
[
  {"dataset_id": 7, "name": "sales_2024", "row_count": 12500, "created_at": "2024-06-01T10:00:00+00:00"}
]
```

---

### Dataset metadata

```
GET /datasets/{id}/info
```

```bash
curl http://localhost:8000/datasets/7/info -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "dataset_id": 7,
  "name": "sales_2024",
  "row_count": 12500,
  "created_at": "2024-06-01T10:00:00+00:00",
  "columns": [
    {"column_name": "region",  "data_type": "text"},
    {"column_name": "revenue", "data_type": "numeric"},
    {"column_name": "date",    "data_type": "date"}
  ],
  "schema": {"columns": [{"name": "region", "type": "text"}, ...]}
}
```

---

## Search

### Full-text search

```
GET /datasets/{id}/search
```

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | required | Search query |
| `columns` | string | all text cols | Comma-separated column names to search |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Results per page (max 100) |

**Query syntax** (passed to PostgreSQL `websearch_to_tsquery`):

| Syntax | Meaning | Example |
|---|---|---|
| `word` | Single keyword | `engineer` |
| `word1 word2` | Both words (implicit AND) | `data science` |
| `"word1 word2"` | Exact phrase | `"machine learning"` |
| `word1 OR word2` | Either word | `python OR java` |
| `word1 -word2` | First but NOT second | `engineer -manager` |

PostgreSQL applies English stemming automatically: querying `running` also matches `run`, `runs`, `runner`.

```bash
# Simple keyword
curl "http://localhost:8000/datasets/7/search?q=london" -H "X-User-Id: 1"

# Phrase
curl "http://localhost:8000/datasets/7/search?q=%22new+york%22" -H "X-User-Id: 1"

# Boolean
curl "http://localhost:8000/datasets/7/search?q=python+OR+ruby&columns=skills" -H "X-User-Id: 1"

# Paginated
curl "http://localhost:8000/datasets/7/search?q=sales&page=2&page_size=50" -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "total": 143,
  "page": 1,
  "page_size": 20,
  "rows": [
    {"id": 42, "data": {"name": "Alice", "role": "Sales Engineer"}, "score": 0.0759},
    ...
  ]
}
```

`score` is `ts_rank` — higher = more relevant. Results are sorted descending.

---

## Filtering

### Structured query

```
GET /datasets/{id}/query
```

| Param | Type | Default | Description |
|---|---|---|---|
| `filters` | JSON string | none | Filter array (see below) |
| `logic` | `AND` \| `OR` | `AND` | How filters are combined |
| `sort_by` | string | none | Column to sort by |
| `sort_dir` | `ASC` \| `DESC` | `ASC` | Sort direction |
| `page` | int | 1 | |
| `page_size` | int | 20 | Max 100 |

**Filter object**

```json
{"column": "<name>", "operator": "<op>", "value": <value>}
```

**Operators**

| Operator | Type | Example |
|---|---|---|
| `=` | equality | `{"column":"city","operator":"=","value":"NYC"}` |
| `!=` | not equal | `{"column":"status","operator":"!=","value":"inactive"}` |
| `>` `<` `>=` `<=` | numeric/date comparison | `{"column":"age","operator":">","value":30}` |
| `IN` | set membership | `{"column":"region","operator":"IN","value":["East","West"]}` |
| `LIKE` | pattern (case-sensitive) | `{"column":"name","operator":"LIKE","value":"Ali%"}` |
| `ILIKE` | pattern (case-insensitive) | `{"column":"name","operator":"ILIKE","value":"%alice%"}` |
| `IS NULL` | null check | `{"column":"email","operator":"IS NULL"}` |
| `IS NOT NULL` | non-null check | `{"column":"email","operator":"IS NOT NULL"}` |

```bash
# Single filter
curl 'http://localhost:8000/datasets/7/query?filters=[{"column":"age","operator":">","value":30}]' \
  -H "X-User-Id: 1"

# Multiple filters with OR
curl 'http://localhost:8000/datasets/7/query?\
filters=[{"column":"city","operator":"=","value":"NYC"},{"column":"city","operator":"=","value":"LA"}]\
&logic=OR' \
  -H "X-User-Id: 1"

# Sorted
curl 'http://localhost:8000/datasets/7/query?filters=[{"column":"score","operator":">=","value":90}]\
&sort_by=score&sort_dir=DESC' \
  -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "total": 38,
  "page": 1,
  "page_size": 20,
  "rows": [
    {"id": 12, "data": {"name": "Alice", "age": 32, "city": "NYC", "score": 97.5}},
    ...
  ]
}
```

---

## Aggregation

### GROUP BY with metrics

```
GET /datasets/{id}/aggregate
```

| Param | Type | Required | Description |
|---|---|---|---|
| `group_by` | string | yes | Comma-separated columns |
| `metrics` | string | yes | Comma-separated metric expressions |
| `filters` | JSON string | no | Pre-aggregation filters |
| `sort_by` | string | no | Metric alias or group-by column |
| `sort_dir` | string | `DESC` | `ASC` or `DESC` |
| `time_column` | string | no | Enable time-series mode |
| `time_truncate` | string | `month` | `day` \| `week` \| `month` \| `quarter` \| `year` |

**Metric syntax**: `FUNCTION(column)` where function is `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, or `STDDEV`.  
Use `COUNT(*)` to count all rows regardless of a specific column.

```bash
# Revenue by region
curl "http://localhost:8000/datasets/7/aggregate?\
group_by=region&metrics=SUM(revenue),AVG(revenue),COUNT(*)" \
  -H "X-User-Id: 1"

# Top departments by headcount (filtered to active employees)
curl 'http://localhost:8000/datasets/7/aggregate?\
group_by=department\
&metrics=COUNT(*),AVG(salary)\
&filters=[{"column":"status","operator":"=","value":"active"}]\
&sort_by=count_all&sort_dir=DESC' \
  -H "X-User-Id: 1"

# Monthly revenue time-series
curl "http://localhost:8000/datasets/7/aggregate?\
time_column=order_date&time_truncate=month&metrics=SUM(revenue)" \
  -H "X-User-Id: 1"
```

**Response 200** (GROUP BY mode)
```json
{
  "rows": [
    {"region": "East", "sum_revenue": 425000.0, "avg_revenue": 8500.0, "count_all": 50},
    {"region": "West", "sum_revenue": 312000.0, "avg_revenue": 6240.0, "count_all": 50}
  ]
}
```

**Response 200** (time-series mode)
```json
{
  "rows": [
    {"period": "2024-01-01 00:00:00+00:00", "sum_revenue": 89000.0},
    {"period": "2024-02-01 00:00:00+00:00", "sum_revenue": 103000.0}
  ]
}
```

---

## Statistics

### SQL-level stats (all columns)

```
GET /datasets/{id}/stats
GET /datasets/{id}/stats?column=<name>
```

Returns count, null count, distinct count. Numeric columns also include min/max/avg/stddev. Text includes min/max length. Date includes min/max date.

```bash
curl http://localhost:8000/datasets/7/stats -H "X-User-Id: 1"
curl "http://localhost:8000/datasets/7/stats?column=revenue" -H "X-User-Id: 1"
```

---

### Advanced numpy stats (single column)

```
GET /datasets/{id}/stats/{column}
```

Returns full descriptive statistics including skewness, kurtosis, and all percentiles. Column must be `numeric`.

```bash
curl http://localhost:8000/datasets/7/stats/revenue -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "column": "revenue",
  "count": 12500,
  "mean": 8742.3,
  "median": 7800.0,
  "std": 3241.5,
  "variance": 10507315.0,
  "min": 500.0,
  "max": 89000.0,
  "skewness": 1.23,
  "kurtosis": 2.87,
  "quantiles": {"p25": 6200.0, "p50": 7800.0, "p75": 10500.0, "p90": 14000.0, "p99": 32000.0}
}
```

---

### Value distribution (histogram)

```
GET /datasets/{id}/distribution/{column}?buckets=20
```

| Param | Default | Range |
|---|---|---|
| `buckets` | 20 | 2–100 |

```bash
curl "http://localhost:8000/datasets/7/distribution/revenue?buckets=10" -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "column": "revenue",
  "buckets": 10,
  "total_values": 12500,
  "counts": [245, 1823, 3100, 2900, 2100, 1200, 700, 280, 110, 42],
  "bin_edges": [500.0, 9350.0, 18200.0, ...],
  "bin_centres": [4925.0, 13775.0, ...]
}
```

---

### Correlation matrix

```
GET /datasets/{id}/correlations
```

Computes Pearson correlation for all numeric column pairs using `np.corrcoef`.

```bash
curl http://localhost:8000/datasets/7/correlations -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "columns": ["age", "salary", "tenure"],
  "matrix": [
    {"column": "age",    "correlations": {"age": 1.0,  "salary": 0.72, "tenure": 0.68}},
    {"column": "salary", "correlations": {"age": 0.72, "salary": 1.0,  "tenure": 0.54}},
    {"column": "tenure", "correlations": {"age": 0.68, "salary": 0.54, "tenure": 1.0}}
  ]
}
```

---

### Outlier detection

```
GET /datasets/{id}/outliers/{column}
```

Uses the Tukey IQR fence method: values outside `Q1 − 1.5×IQR` or `Q3 + 1.5×IQR` are outliers.

```bash
curl http://localhost:8000/datasets/7/outliers/revenue -H "X-User-Id: 1"
```

**Response 200**
```json
{
  "column": "revenue",
  "method": "IQR (Tukey fences)",
  "q1": 6200.0, "q3": 10500.0, "iqr": 4300.0,
  "lower_fence": -250.0, "upper_fence": 16950.0,
  "sample_size": 12500,
  "outlier_count": 152,
  "outliers": [
    {"id": 4821, "value": 89000},
    ...
  ]
}
```

---

## Export

```
GET /datasets/{id}/export
```

| Param | Default | Description |
|---|---|---|
| `format` | `csv` | `csv` or `json` |
| `filters` | none | JSON filter array (same format as `/query`) |
| `page_size` | 10000 | Max rows to export (up to 100000) |

```bash
# Export all rows as CSV
curl "http://localhost:8000/datasets/7/export?format=csv" \
  -H "X-User-Id: 1" -o export.csv

# Export filtered subset as JSON
curl 'http://localhost:8000/datasets/7/export?format=json\
&filters=[{"column":"region","operator":"=","value":"East"}]' \
  -H "X-User-Id: 1" -o east_region.json
```

---

## Error responses

| Code | Meaning |
|---|---|
| 400 | Bad request — invalid filter syntax, unsupported operator, empty query |
| 401 | Missing `X-User-Id` header |
| 404 | Dataset not found, or column not found |
| 422 | Validation error — e.g. requesting numpy stats on a non-numeric column |
| 500 | Internal server error |

All errors return `{"detail": "<message>"}`.
