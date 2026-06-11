-- data-lens schema
-- Run migrations/001_initial_schema.sql to apply this in a versioned way.
-- This file is the canonical reference for the full schema at HEAD.

-- ─── Extensions ──────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS btree_gin;   -- GIN indexes on JSONB + btree types together
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram similarity for fulltext-style LIKE queries

-- ─── Enums ───────────────────────────────────────────────────────────────────

CREATE TYPE column_data_type AS ENUM ('numeric', 'text', 'date');
CREATE TYPE search_index_type AS ENUM ('btree', 'hash', 'fulltext');

-- ─── Tables ──────────────────────────────────────────────────────────────────

CREATE TABLE users (
    id          BIGSERIAL    PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE datasets (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    -- shape: {"columns": [{"name": "age", "type": "numeric"}, ...]}
    schema      JSONB        NOT NULL DEFAULT '{}',
    row_count   INTEGER      NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE dataset_columns (
    id           BIGSERIAL         PRIMARY KEY,
    dataset_id   BIGINT            NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    column_name  VARCHAR(255)      NOT NULL,
    data_type    column_data_type  NOT NULL,
    created_at   TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_dataset_column_name UNIQUE (dataset_id, column_name)
);

CREATE TABLE dataset_rows (
    id          BIGSERIAL  PRIMARY KEY,
    dataset_id  BIGINT     NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    -- mirrors dataset schema: {"age": 30, "name": "Alice", "joined": "2024-01-01"}
    data        JSONB      NOT NULL
);

CREATE TABLE search_indexes (
    id           BIGSERIAL         PRIMARY KEY,
    dataset_id   BIGINT            NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    column_name  VARCHAR(255)      NOT NULL,
    index_type   search_index_type NOT NULL,

    CONSTRAINT uq_search_index UNIQUE (dataset_id, column_name, index_type)
);

-- ─── Indexes ─────────────────────────────────────────────────────────────────

-- Users
CREATE INDEX idx_users_email ON users (email);

-- Datasets
CREATE INDEX idx_datasets_user_id ON datasets (user_id);

-- Dataset columns
CREATE INDEX idx_dataset_columns_dataset_id ON dataset_columns (dataset_id);

-- Dataset rows: the main workhorse
--
-- GIN index on the entire JSONB document. Supports:
--   data @> '{"city": "NYC"}'           -- containment (most common analytics filter)
--   data ? 'some_key'                   -- key existence checks
--   jsonb_path_exists(data, '$.age ...) -- JSONPath queries
--
-- Trade-off: GIN is slow to write, fast to read. Fine for bulk-loaded CSVs.
CREATE INDEX idx_dataset_rows_data_gin ON dataset_rows USING GIN (data);

-- Partial btree on a concrete extracted field — template, not applied as-is.
-- When a column is declared numeric in dataset_columns, a concrete index like this
-- should be created dynamically (see search_indexes table + application layer):
--   CREATE INDEX idx_dataset_rows_<col>
--       ON dataset_rows ((data->>'<col>') DESC NULLS LAST)
--       WHERE dataset_id = <id>;
--
-- Trigram index for fulltext columns — enables fast LIKE '%keyword%':
--   CREATE INDEX idx_dataset_rows_<col>_trgm
--       ON dataset_rows USING GIN ((data->>'<col>') gin_trgm_ops)
--       WHERE dataset_id = <id>;

-- Search index registry
CREATE INDEX idx_search_indexes_dataset_id ON search_indexes (dataset_id);
