"""Storage: stories, AI analyses, and an append-only audit log.

Uses Postgres when DATABASE_URL is set (the hosted copy on Vercel + Neon), and
a local SQLite file otherwise (running on your PC, and the tests). Both enforce
the same rule: audit_log rows can be inserted but never updated or deleted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    requirement_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL REFERENCES stories(id),
    result TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    final_risk TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    story_id INTEGER,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    username TEXT NOT NULL,
    ip TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL UNIQUE REFERENCES analyses(id),
    signer TEXT NOT NULL,
    meaning TEXT NOT NULL,
    signed_at TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS signatures_no_update BEFORE UPDATE ON signatures
BEGIN SELECT RAISE(ABORT, 'signatures are permanent'); END;
CREATE TRIGGER IF NOT EXISTS signatures_no_delete BEFORE DELETE ON signatures
BEGIN SELECT RAISE(ABORT, 'signatures are permanent'); END;
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    test_id TEXT NOT NULL,
    result TEXT NOT NULL,
    notes TEXT NOT NULL,
    evidence_id INTEGER REFERENCES evidence(id),
    executed_by TEXT NOT NULL,
    executed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS test_runs_no_update BEFORE UPDATE ON test_runs
BEGIN SELECT RAISE(ABORT, 'test_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS test_runs_no_delete BEFORE DELETE ON test_runs
BEGIN SELECT RAISE(ABORT, 'test_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id BIGSERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    requirement_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id BIGSERIAL PRIMARY KEY,
    story_id BIGINT NOT NULL REFERENCES stories(id),
    result TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    final_risk TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    story_id BIGINT,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    id BIGSERIAL PRIMARY KEY,
    at DOUBLE PRECISION NOT NULL,
    username TEXT NOT NULL,
    ip TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signatures (
    id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL UNIQUE REFERENCES analyses(id),
    signer TEXT NOT NULL,
    meaning TEXT NOT NULL,
    signed_at TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    data BYTEA NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id BIGSERIAL PRIMARY KEY,
    analysis_id BIGINT NOT NULL REFERENCES analyses(id),
    test_id TEXT NOT NULL,
    result TEXT NOT NULL,
    notes TEXT NOT NULL,
    evidence_id BIGINT REFERENCES evidence(id),
    executed_by TEXT NOT NULL,
    executed_at TEXT NOT NULL
);
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER signatures_no_update BEFORE UPDATE ON signatures
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER signatures_no_delete BEFORE DELETE ON signatures
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER signatures_no_truncate BEFORE TRUNCATE ON signatures
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER evidence_no_update BEFORE UPDATE ON evidence
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER evidence_no_delete BEFORE DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER evidence_no_truncate BEFORE TRUNCATE ON evidence
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER test_runs_no_update BEFORE UPDATE ON test_runs
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER test_runs_no_delete BEFORE DELETE ON test_runs
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
CREATE OR REPLACE TRIGGER test_runs_no_truncate BEFORE TRUNCATE ON test_runs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Result:
    """Rows fetched eagerly, so the connection lock is released right away."""

    def __init__(self, rows: list, lastrowid: int | None = None):
        self.rows = rows
        self.lastrowid = lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class SqliteDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SQLITE_SCHEMA)
        self.lock = threading.Lock()

    def execute(self, sql: str, args: tuple = ()) -> Result:
        with self.lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            self.conn.commit()
            return Result(rows, cur.lastrowid)

    def commit(self) -> None:  # every execute already commits
        pass


class PostgresDB:
    """One autocommit connection per instance, reopened if it drops.

    SQL in main.py uses SQLite's "?" placeholders; they are converted here.
    INSERTs get "RETURNING id" so lastrowid works the same way.
    """

    def __init__(self, url: str):
        self.url = url
        self.lock = threading.Lock()
        self.conn = None
        self._connect()
        with self.conn.transaction():
            # serialise schema setup across instances that start at the same time
            self.conn.execute("SELECT pg_advisory_xact_lock(724411)")
            self.conn.execute(POSTGRES_SCHEMA)

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row,
                                    prepare_threshold=None)  # pooled (pgbouncer) URLs can't keep prepared statements
        self.last_used = time.monotonic()

    def execute(self, sql: str, args: tuple = ()) -> Result:
        import psycopg

        sql = sql.replace("?", "%s")
        is_insert = sql.lstrip().upper().startswith("INSERT")
        if is_insert:
            sql += " RETURNING id"
        is_read = sql.lstrip().upper().startswith("SELECT")
        with self.lock:
            self._ensure_alive()
            for attempt in (1, 2):
                try:
                    cur = self.conn.execute(sql, args)
                    rows = cur.fetchall() if cur.description else []
                    break
                except psycopg.OperationalError:
                    # Only reads are retried: a write may have landed before the
                    # connection dropped, and the audit trail must not get duplicates.
                    if attempt == 2 or not is_read:
                        raise
                    self._connect()
            self.last_used = time.monotonic()
        return Result(rows, rows[0]["id"] if is_insert and rows else None)

    def _ensure_alive(self) -> None:
        import psycopg

        if self.conn.closed or self.conn.broken:
            self._connect()
        elif time.monotonic() - self.last_used > 30:  # idle instance: the server may have closed it
            try:
                self.conn.execute("SELECT 1")
            except psycopg.OperationalError:
                self._connect()

    def commit(self) -> None:  # autocommit
        pass


def database_url() -> str | None:
    # Vercel previews (and `vercel dev`) get their own database, so test data
    # never reaches the live site.
    if os.environ.get("VERCEL_ENV") in ("preview", "development") and os.environ.get("PREVIEW_DATABASE_URL"):
        return os.environ["PREVIEW_DATABASE_URL"]
    return os.environ.get("DATABASE_URL")


def connect(path: str | None = None):
    url = database_url()
    if url and not path:
        return PostgresDB(url)
    return SqliteDB(path or os.environ.get("AVB_DB", "avb.db"))


def log(conn, actor: str, action: str, story_id: int | None, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, story_id, detail) VALUES (?, ?, ?, ?, ?)",
        (now(), actor, action, story_id, json.dumps(detail)),
    )


def is_unique_violation(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE" in str(exc)
    return getattr(exc, "sqlstate", None) == "23505"  # Postgres unique_violation
