import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from analytics.aggregation import aggregate_dataset, parse_metric, time_series_aggregate
from analytics.export import export_to_csv, export_to_json, flatten_row
from analytics.query_builder import execute_query, parse_filter_input
from analytics.stats import column_stats, dataset_stats, get_column_types
from analytics.statistics import (
    basic_stats,
    correlation_matrix,
    distribution_analysis,
    outlier_detection,
    quantiles,
)
from analytics.time_series import growth_rate, moving_average, resample_by_period
from db.models import Dataset, DatasetColumn
from db.session import get_db
from ingestion.pipeline import ingest_dataset
from search.engine import search_dataset

router = APIRouter()


# ─── Auth placeholder ─────────────────────────────────────────────────────────

def current_user_id(x_user_id: Annotated[str | None, Header()] = None) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-Id header required")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-User-Id must be an integer")


# ─── Response models ──────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    dataset_id: int
    row_count: int


class DatasetSummary(BaseModel):
    dataset_id: int
    name: str
    row_count: int
    created_at: str


class ColumnInfo(BaseModel):
    column_name: str
    data_type: str


class DatasetInfo(BaseModel):
    dataset_id: int
    name: str
    row_count: int
    created_at: str
    columns: list[ColumnInfo]
    schema: dict


class SearchRow(BaseModel):
    id: int
    data: dict
    score: float


class SearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[SearchRow]


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/datasets", response_model=UploadResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str = Form(...),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    try:
        dataset = ingest_dataset(
            db=db,
            user_id=user_id,
            file_stream=file.file,
            dataset_name=name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return UploadResponse(dataset_id=dataset.id, row_count=dataset.row_count)


@router.get("/datasets", response_model=list[DatasetSummary])
def list_datasets(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    datasets = db.query(Dataset).filter(Dataset.user_id == user_id).all()
    return [
        DatasetSummary(
            dataset_id=d.id,
            name=d.name,
            row_count=d.row_count,
            created_at=d.created_at.isoformat(),
        )
        for d in datasets
    ]


@router.get("/datasets/{dataset_id}/info", response_model=DatasetInfo)
def dataset_info(
    dataset_id: int,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.user_id == user_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    columns = (
        db.query(DatasetColumn)
        .filter(DatasetColumn.dataset_id == dataset_id)
        .all()
    )

    return DatasetInfo(
        dataset_id=dataset.id,
        name=dataset.name,
        row_count=dataset.row_count,
        created_at=dataset.created_at.isoformat(),
        columns=[
            ColumnInfo(column_name=c.column_name, data_type=c.data_type)
            for c in columns
        ],
        schema=dataset.schema,
    )


@router.get("/datasets/{dataset_id}/search", response_model=SearchResponse)
def search(
    dataset_id: int,
    q: str = Query(..., description='Keywords, "quoted phrase", word1 AND word2, word1 OR word2'),
    columns: str | None = Query(None, description="Comma-separated column names to search"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.user_id == user_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    col_list = [c.strip() for c in columns.split(",")] if columns else None

    try:
        result = search_dataset(
            db=db,
            dataset_id=dataset_id,
            query_str=q,
            columns=col_list,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return SearchResponse(
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        rows=[SearchRow(**row) for row in result.rows],
    )


# ─── Advanced querying ────────────────────────────────────────────────────────


class QueryRow(BaseModel):
    id: int
    data: dict


class QueryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[QueryRow]


@router.get("/datasets/{dataset_id}/query", response_model=QueryResponse)
def query_dataset(
    dataset_id: int,
    filters: str | None = Query(
        None,
        description='JSON filter array: [{"column":"age","operator":">","value":30}]',
    ),
    logic: str = Query("AND", description="AND or OR — joins top-level filters"),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("ASC", description="ASC or DESC"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.user_id == user_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    try:
        filter_group = None
        if filters:
            filter_group = parse_filter_input(filters)
            filter_group.logic = filter_group.logic.__class__(logic.upper())

        result = execute_query(
            db=db,
            dataset_id=dataset_id,
            filter_group=filter_group,
            sort_by=sort_by,
            sort_dir=sort_dir,
            page=page,
            page_size=page_size,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return QueryResponse(
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        rows=[QueryRow(**row) for row in result.rows],
    )


@router.get("/datasets/{dataset_id}/aggregate")
def aggregate(
    dataset_id: int,
    group_by: str = Query(..., description="Comma-separated column names, e.g. region,category"),
    metrics: str = Query(..., description="Comma-separated metrics, e.g. SUM(revenue),AVG(price),COUNT(*)"),
    filters: str | None = Query(None),
    sort_by: str | None = Query(None, description="Metric alias or group-by column"),
    sort_dir: str = Query("DESC"),
    time_column: str | None = Query(None, description="Date column for time-series grouping"),
    time_truncate: str = Query("month", description="day | week | month | quarter | year"),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.user_id == user_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    try:
        parsed_metrics = [parse_metric(m.strip()) for m in metrics.split(",")]
        filter_group = parse_filter_input(filters) if filters else None

        if time_column:
            if len(parsed_metrics) != 1:
                raise ValueError("Time-series aggregation requires exactly one metric")
            rows = time_series_aggregate(
                db=db,
                dataset_id=dataset_id,
                date_column=time_column,
                metric=parsed_metrics[0],
                truncate=time_truncate,
                filter_group=filter_group,
            )
        else:
            group_cols = [c.strip() for c in group_by.split(",")]
            rows = aggregate_dataset(
                db=db,
                dataset_id=dataset_id,
                group_by=group_cols,
                metrics=parsed_metrics,
                filter_group=filter_group,
                sort_by=sort_by,
                sort_dir=sort_dir,
            )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"rows": rows}


@router.get("/datasets/{dataset_id}/stats")
def stats(
    dataset_id: int,
    column: str | None = Query(None, description="Single column name; omit for all columns"),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == dataset_id, Dataset.user_id == user_id)
        .first()
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    try:
        if column:
            col_types = get_column_types(db, dataset_id)
            if column not in col_types:
                raise HTTPException(status_code=404, detail=f"Column '{column}' not found")
            return column_stats(db, dataset_id, column, col_types[column])
        return {"columns": dataset_stats(db, dataset_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─── Statistical analysis ─────────────────────────────────────────────────────


def _require_dataset(db: Session, dataset_id: int, user_id: int) -> Dataset:
    ds = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.user_id == user_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return ds


def _require_numeric_column(db: Session, dataset_id: int, column: str) -> None:
    col_types = get_column_types(db, dataset_id)
    if column not in col_types:
        raise HTTPException(status_code=404, detail=f"Column '{column}' not found")
    if col_types[column] != "numeric":
        raise HTTPException(status_code=422, detail=f"Column '{column}' is not numeric")


@router.get("/datasets/{dataset_id}/stats/{column}")
def column_statistics(
    dataset_id: int,
    column: str,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _require_dataset(db, dataset_id, user_id)
    _require_numeric_column(db, dataset_id, column)
    try:
        result = basic_stats(db, dataset_id, column)
        q = quantiles(db, dataset_id, column)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="No non-null values found")
    return {**result, "quantiles": q}


@router.get("/datasets/{dataset_id}/distribution/{column}")
def column_distribution(
    dataset_id: int,
    column: str,
    buckets: int = Query(20, ge=2, le=100),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _require_dataset(db, dataset_id, user_id)
    _require_numeric_column(db, dataset_id, column)
    result = distribution_analysis(db, dataset_id, column, buckets=buckets)
    if result is None:
        raise HTTPException(status_code=404, detail="No non-null values found")
    return result


@router.get("/datasets/{dataset_id}/correlations")
def correlations(
    dataset_id: int,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _require_dataset(db, dataset_id, user_id)
    return correlation_matrix(db, dataset_id)


@router.get("/datasets/{dataset_id}/outliers/{column}")
def outliers(
    dataset_id: int,
    column: str,
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _require_dataset(db, dataset_id, user_id)
    _require_numeric_column(db, dataset_id, column)
    result = outlier_detection(db, dataset_id, column)
    if result is None:
        raise HTTPException(status_code=404, detail="No non-null values found")
    return result


@router.get("/datasets/{dataset_id}/export")
def export_dataset(
    dataset_id: int,
    format: str = Query("csv", description="csv or json"),
    filters: str | None = Query(None),
    page_size: int = Query(10_000, ge=1, le=100_000),
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    _require_dataset(db, dataset_id, user_id)

    try:
        filter_group = parse_filter_input(filters) if filters else None
        result = execute_query(
            db=db,
            dataset_id=dataset_id,
            filter_group=filter_group,
            page=1,
            page_size=page_size,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    flat_rows = [flatten_row(r) for r in result.rows]
    fmt = format.lower()

    if fmt == "csv":
        content = export_to_csv(flat_rows)
        return StreamingResponse(
            io.StringIO(content),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="dataset_{dataset_id}.csv"'
            },
        )

    if fmt == "json":
        return StreamingResponse(
            io.StringIO(export_to_json(flat_rows)),
            media_type="application/json",
        )

    raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'")
