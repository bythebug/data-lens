from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from ingestion import ingest_dataset
from models import Dataset, DatasetColumn
from search import search_dataset

app = FastAPI(title="data-lens")


# ─── Auth placeholder ─────────────────────────────────────────────────────────
# No auth layer yet — caller passes X-User-Id header.
# Replace with real JWT/session validation when auth is added.

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


@app.post("/datasets", response_model=UploadResponse, status_code=201)
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


@app.get("/datasets", response_model=list[DatasetSummary])
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


@app.get("/datasets/{dataset_id}/info", response_model=DatasetInfo)
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


@app.get("/datasets/{dataset_id}/search", response_model=SearchResponse)
def search(
    dataset_id: int,
    q: str = Query(..., description='Keywords, "quoted phrase", word1 AND word2, word1 OR word2'),
    columns: str | None = Query(None, description="Comma-separated column names to search (default: all text columns)"),
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
