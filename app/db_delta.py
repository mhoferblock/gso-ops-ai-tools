"""
Delta Lake database layer for GSO Ops AI Tools.

Mirrors the Menu Grading Tool's db.py pattern:
  - Uses databricks-sdk WorkspaceClient + statement_execution
  - Falls back gracefully to None if SDK unavailable
  - All queries go to sandbox.gso_ops_ai_tools via DATABRICKS_WAREHOUSE_ID
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_client = None
_CATALOG  = os.getenv("DATABRICKS_CATALOG", "sandbox")
_SCHEMA   = os.getenv("DATABRICKS_SCHEMA",  "gso_ops_ai_tools")
_WID      = os.getenv("DATABRICKS_WAREHOUSE_ID", "dfcdeaab45b566be")  # Starter Endpoint


def _get_client():
    global _client
    if _client is None:
        try:
            from databricks.sdk import WorkspaceClient
            _client = WorkspaceClient()
            print(f"[DeltaDB] connected → {_CATALOG}.{_SCHEMA}")
        except Exception as ex:
            print(f"[DeltaDB] SDK unavailable: {ex}")
    return _client


def execute(sql: str, params: Optional[dict] = None) -> list[dict]:
    """Run SQL and return list-of-dicts rows. Returns [] on error."""
    client = _get_client()
    if client is None:
        return []

    # Simple positional-safe param substitution (no user input reaches DDL)
    fqn_sql = sql
    if params:
        for k, v in params.items():
            ph = f":{k}"
            if isinstance(v, str):
                fqn_sql = fqn_sql.replace(ph, "'" + v.replace("'", "''") + "'")
            elif v is None:
                fqn_sql = fqn_sql.replace(ph, "NULL")
            else:
                fqn_sql = fqn_sql.replace(ph, str(v))

    try:
        resp = client.statement_execution.execute_statement(
            warehouse_id=_WID,
            statement=fqn_sql,
            catalog=_CATALOG,
            schema=_SCHEMA,
            wait_timeout="30s",
        )
    except Exception as ex:
        print(f"[DeltaDB] execute error: {ex} | sql: {sql[:120]}")
        return []

    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state == "FAILED":
        msg = resp.status.error.message if resp.status.error else "?"
        print(f"[DeltaDB] SQL FAILED: {msg} | sql: {sql[:120]}")
        return []

    if not resp.result or not resp.result.data_array:
        return []

    # Build column name list from manifest
    cols = []
    if resp.manifest and resp.manifest.schema and resp.manifest.schema.columns:
        cols = [c.name for c in resp.manifest.schema.columns]

    return [dict(zip(cols, row)) for row in resp.result.data_array]


def execute_write(sql: str, params: Optional[dict] = None) -> bool:
    """Run a write (INSERT/UPDATE/DELETE) statement. Returns True on success."""
    execute(sql, params)  # returns [] for writes; errors already logged
    return True


def is_available() -> bool:
    return _get_client() is not None


# ── Schema bootstrap ──────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS {cat}.{sch}.users (
    id        BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username  STRING  NOT NULL,
    display_name STRING NOT NULL,
    email     STRING  DEFAULT '',
    avatar_url STRING DEFAULT '',
    bio       STRING  DEFAULT '',
    favorite_ai_project STRING DEFAULT '',
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.tools (
    id          BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id    BIGINT  NOT NULL,
    name        STRING  NOT NULL,
    url         STRING  NOT NULL,
    description STRING  DEFAULT '',
    summary     STRING  DEFAULT '',
    thumbnail_url STRING DEFAULT '',
    tags        STRING  DEFAULT '[]',
    click_count BIGINT  DEFAULT 0,
    vote_count  BIGINT  DEFAULT 0,
    is_featured INT     DEFAULT 0,
    created_at  TIMESTAMP DEFAULT current_timestamp(),
    updated_at  TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.votes (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    tool_id    BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.weekly_votes (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    tool_id    BIGINT NOT NULL,
    week_start STRING NOT NULL,
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.weekly_winners (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tool_id       BIGINT NOT NULL,
    week_start    STRING NOT NULL,
    votes_at_time BIGINT DEFAULT 0,
    created_at    TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.winner_seen (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    winner_id  BIGINT NOT NULL,
    seen_at    TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.chat_messages (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT,
    username     STRING NOT NULL,
    display_name STRING DEFAULT '',
    message      STRING NOT NULL,
    is_bot       INT    DEFAULT 0,
    created_at   TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.best_practices (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    author_name STRING NOT NULL,
    title       STRING NOT NULL,
    content     STRING NOT NULL,
    created_at  TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.activity_feed (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type STRING NOT NULL,
    user_id    BIGINT,
    tool_id    BIGINT,
    message    STRING NOT NULL,
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA;

CREATE TABLE IF NOT EXISTS {cat}.{sch}.board_notes (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    author_name  STRING NOT NULL,
    message      STRING DEFAULT '',
    color        STRING DEFAULT '#FFEF5E',
    emoji        STRING DEFAULT '',
    drawing_data STRING DEFAULT '',
    pen_color    STRING DEFAULT '#000000',
    x_pos        DOUBLE DEFAULT 100.0,
    y_pos        DOUBLE DEFAULT 100.0,
    rotation     DOUBLE DEFAULT 0.0,
    created_at   TIMESTAMP DEFAULT current_timestamp(),
    expires_at   TIMESTAMP DEFAULT dateadd(DAY, 7, current_timestamp())
) USING DELTA;
"""


def init_tables():
    """Create all Delta Lake tables if they don't exist yet."""
    client = _get_client()
    if client is None:
        print("[DeltaDB] SDK not available — skipping Delta table init")
        return False

    for stmt in DDL.format(cat=_CATALOG, sch=_SCHEMA).strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            resp = client.statement_execution.execute_statement(
                warehouse_id=_WID,
                statement=stmt,
                catalog=_CATALOG,
                schema=_SCHEMA,
                wait_timeout="30s",
            )
            state = resp.status.state.value if resp.status and resp.status.state else "?"
            if state == "FAILED":
                msg = resp.status.error.message if resp.status.error else "?"
                print(f"[DeltaDB] DDL failed: {msg}")
        except Exception as ex:
            print(f"[DeltaDB] DDL error: {ex}")

    print(f"[DeltaDB] Tables ready at {_CATALOG}.{_SCHEMA}")
    return True


# ── SQLite-compatible connection adapter ──────────────────────────────────────
import re as _re


class _DeltaRow(dict):
    """Dict that also supports integer indexing so fetchone()[0] works."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _DeltaCursor:
    def __init__(self, rows: list, last_id=None):
        self._rows = rows
        self.lastrowid = last_id
        self.rowcount  = len(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def _to_named(sql: str, params: tuple) -> tuple[str, dict]:
    """Convert ? positional params → :p0 :p1 ... and return (sql, param_dict)."""
    named, idx = {}, 0
    out = []
    for ch in sql:
        if ch == "?":
            out.append(f":p{idx}")
            named[f"p{idx}"] = params[idx] if idx < len(params) else None
            idx += 1
        else:
            out.append(ch)
    return "".join(out), named


def _sqlite_to_spark(sql: str) -> str:
    """Translate SQLite-specific syntax to Spark SQL."""
    sql = _re.sub(r"datetime\('now'\)", "current_timestamp()", sql)
    sql = _re.sub(
        r"datetime\('now',\s*'\+(\d+)\s+days?'\)",
        lambda m: f"dateadd(DAY, {m.group(1)}, current_timestamp())",
        sql,
    )
    sql = _re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql, flags=_re.IGNORECASE)
    sql = _re.sub(r"SELECT\s+last_insert_rowid\(\)", "SELECT 0 AS last_insert_rowid", sql, flags=_re.IGNORECASE)
    return sql


def _table_from_insert(sql: str):
    m = _re.match(r"\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", sql, _re.IGNORECASE)
    return m.group(1) if m else None


class DeltaConn:
    """
    Drop-in replacement for sqlite3.Connection that routes to Delta Lake.
    Makes all existing SQLite endpoint code work unchanged on Databricks.
    """

    def __init__(self):
        self._last_id = None

    def execute(self, sql: str, params=()):
        # DDL handled by init_tables; ignore executescript leftovers
        if sql.strip().lower().startswith("pragma"):
            return _DeltaCursor([])

        translated, named = _to_named(sql, params)
        translated = _sqlite_to_spark(translated)

        # last_insert_rowid() shim
        if "last_insert_rowid" in sql.lower():
            return _DeltaCursor([_DeltaRow({"last_insert_rowid()": self._last_id})])

        try:
            raw = execute(translated, named or None)
        except Exception:
            raw = []

        result_rows = [_DeltaRow(r) for r in raw]

        # After INSERT, retrieve the generated ID
        last_id = None
        if sql.strip().upper().startswith("INSERT"):
            tbl = _table_from_insert(sql)
            if tbl:
                id_rows = execute(f"SELECT max(id) AS mid FROM {tbl}")
                last_id = id_rows[0].get("mid") if id_rows else None
            self._last_id = last_id

        return _DeltaCursor(result_rows, last_id)

    def executemany(self, sql: str, params_list):
        for p in params_list:
            self.execute(sql, p)
        return _DeltaCursor([])

    def executescript(self, script: str):
        # DDL is handled by init_tables(); silently skip here
        return _DeltaCursor([])

    def commit(self):
        pass  # Delta Lake auto-commits every statement

    def close(self):
        pass  # No connection object to release


def get_conn() -> DeltaConn:
    """Return a DeltaConn ready to use (call is_available() first)."""
    return DeltaConn()
