-- Migration: 001_initial_schema
-- Description: Create initial data-lens schema
-- Applied: manually track in schema_migrations table (see below)

BEGIN;

-- ─── Migration tracking table (bootstrapped in first migration) ───────────────

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(14)  PRIMARY KEY,          -- YYYYMMDDHHMMSS
    description TEXT         NOT NULL,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─── Extensions ──────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

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

CREATE INDEX idx_users_email              ON users        (email);
CREATE INDEX idx_datasets_user_id         ON datasets     (user_id);
CREATE INDEX idx_dataset_columns_ds_id    ON dataset_columns (dataset_id);
CREATE INDEX idx_dataset_rows_data_gin    ON dataset_rows USING GIN (data);
CREATE INDEX idx_search_indexes_ds_id     ON search_indexes  (dataset_id);

-- ─── Record this migration ────────────────────────────────────────────────────

INSERT INTO schema_migrations (version, description)
VALUES ('20240101000000', 'initial schema: users, datasets, dataset_columns, dataset_rows, search_indexes');

COMMIT;
