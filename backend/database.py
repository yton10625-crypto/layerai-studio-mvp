"""
database.py
SQLite persistence layer.

PRD specifies PostgreSQL (section 16). This MVP skeleton uses SQLite because
it ships with Python's standard library and needs no install step — swap
DATABASE in production by replacing this module with a SQLAlchemy + psycopg2
engine pointed at Postgres. The schema below mirrors PRD section 15
(Database Entities) and section 13 (Data Model) so that swap is mechanical.
"""

import sqlite3
import json
import os
import time
import uuid

DB_PATH = os.path.join(os.path.dirname(__file__), "storage", "layerai.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    original_image_path TEXT NOT NULL,
    image_width INTEGER,
    image_height INTEGER,
    status TEXT NOT NULL DEFAULT 'uploaded',  -- uploaded -> analyzing -> analyzed -> editing -> exported
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS layers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,                 -- null = top-level group (Project root)
    name TEXT NOT NULL,
    role TEXT,                      -- headline / subheadline / price / cta / logo / hero_product / background / decoration
    type TEXT NOT NULL,             -- text | image | group
    content_text TEXT,
    font_family_guess TEXT,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    z_index INTEGER NOT NULL DEFAULT 0,
    visible INTEGER NOT NULL DEFAULT 1,
    confidence REAL,
    source_image_path TEXT,         -- for image-type layers (cropped asset)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS ocr_data (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    confidence REAL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS layout_structures (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    structure_json TEXT NOT NULL,   -- {headline:{}, subheadline:{}, price:{}, cta:{}, logo:{}, hero_product:{}}
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS user_modifications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    layer_id TEXT,
    field TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    action TEXT NOT NULL,           -- rename | delete | merge | reorder | edit_text | replace_image
    timestamp REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    export_type TEXT NOT NULL,      -- psd | json
    file_path TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def new_id(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now():
    return time.time()


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
