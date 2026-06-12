FROM python:3.13-slim AS base

WORKDIR /app

# System deps for psycopg2-binary and scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ── Dependencies layer (cached unless requirements.txt changes) ───────────────
FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Final image ───────────────────────────────────────────────────────────────
FROM deps AS app
COPY . .

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# DATABASE_URL must be set via environment variable or docker-compose
ENV DATABASE_URL=postgresql://postgres:postgres@db:5432/data_lens

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
