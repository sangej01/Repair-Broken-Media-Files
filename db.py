"""Database interface — supports SQLite (default) and PostgreSQL backends.

All callers continue to use `db.init_db()`, `db.upsert_file_record(conn, ...)`,
etc. The connection object returned by `init_db()` carries its backend type
internally so each function dispatches to the correct implementation.

Backend selection is via DB_BACKEND in .env:
  - 'sqlite'   (default) — local repair.db, single-PC use
  - 'postgres'           — shared LAN database, multi-PC distributed scanning

The PostgreSQL backend uses tables `repair_files` and `repair_runs` to avoid
collision with `Movie-Library-Compressor`'s tables in the same database.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import (
    DB_BACKEND,
    DB_PATH,
    DATABASE_URL,
    POSTGRES_HOST_CANDIDATES,
    WORKER_ID,
)


# =============================================================================
#  Connection wrapper — carries backend identity alongside the actual conn
# =============================================================================

class RepairDBConnection:
    """Wraps a backend-specific connection plus its type tag.

    Existing code does `conn = db.init_db(); db.upsert_file_record(conn, ...)`.
    To keep that pattern working, the dispatch functions inspect `conn.backend`
    and route to the right implementation.
    """

    def __init__(self, backend: str, raw_conn: Any):
        self.backend = backend       # 'sqlite' or 'postgres'
        self.raw = raw_conn          # sqlite3.Connection OR psycopg2 connection

    # Convenience: forward execute/commit/close so simple callers still work
    def execute(self, *args, **kwargs):
        if self.backend == "sqlite":
            return self.raw.execute(*args, **kwargs)
        # psycopg2 has no top-level execute; callers should use cursor()
        cur = self.raw.cursor()
        cur.execute(*args, **kwargs)
        return cur

    def commit(self):
        self.raw.commit()

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass

    def cursor(self):
        return self.raw.cursor()


# =============================================================================
#  Public API — init_db()
# =============================================================================

def init_db() -> RepairDBConnection:
    """Initialize the database and return a connection wrapper.

    Routes to SQLite or PostgreSQL based on DB_BACKEND in config.
    """
    if DB_BACKEND == "postgres":
        return _init_postgres()
    return _init_sqlite()


# =============================================================================
#  SQLite backend
# =============================================================================

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_path     TEXT    NOT NULL UNIQUE,
    video_path      TEXT,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    duration_sec    REAL,
    scan_state      TEXT    NOT NULL DEFAULT 'UNKNOWN',
    last_scan_at    TEXT,
    last_scan_secs  REAL,
    stderr_tail     TEXT,
    radarr_movie_id INTEGER,
    radarr_tmdb_id  INTEGER,
    remediation     TEXT    NOT NULL DEFAULT 'NONE',
    remediation_at  TEXT,
    remediation_log TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TEXT    NOT NULL,
    notes           TEXT,
    worker_id       TEXT,
    lock_until      TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_scan_state  ON files(scan_state);
CREATE INDEX IF NOT EXISTS idx_files_remediation ON files(remediation);
-- Note: idx_files_lock_until is created after the ALTER TABLE migration in _init_sqlite()
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT    NOT NULL,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    folders_total  INTEGER NOT NULL DEFAULT 0,
    folders_done   INTEGER NOT NULL DEFAULT 0,
    clean_count    INTEGER NOT NULL DEFAULT 0,
    corrupt_count  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    empty_count    INTEGER NOT NULL DEFAULT 0,
    args_json      TEXT,
    notes          TEXT
);
"""


def _init_sqlite() -> RepairDBConnection:
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row
    raw.executescript(_SQLITE_SCHEMA)
    # Migrate existing databases that predate worker_id/lock_until columns.
    # `ALTER TABLE ... ADD COLUMN` fails if the column already exists, so we
    # check first.
    cols = {r["name"] for r in raw.execute("PRAGMA table_info(files)").fetchall()}
    if "worker_id" not in cols:
        raw.execute("ALTER TABLE files ADD COLUMN worker_id TEXT")
    if "lock_until" not in cols:
        raw.execute("ALTER TABLE files ADD COLUMN lock_until TEXT")
    raw.execute("CREATE INDEX IF NOT EXISTS idx_files_lock_until ON files(lock_until)")
    raw.commit()
    return RepairDBConnection("sqlite", raw)


# =============================================================================
#  PostgreSQL backend
# =============================================================================

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS repair_files (
    id              SERIAL PRIMARY KEY,
    folder_path     TEXT    NOT NULL UNIQUE,
    video_path      TEXT,
    size_bytes      BIGINT  NOT NULL DEFAULT 0,
    duration_sec    DOUBLE PRECISION,
    scan_state      TEXT    NOT NULL DEFAULT 'UNKNOWN',
    last_scan_at    TIMESTAMPTZ,
    last_scan_secs  DOUBLE PRECISION,
    stderr_tail     TEXT,
    radarr_movie_id INTEGER,
    radarr_tmdb_id  INTEGER,
    remediation     TEXT    NOT NULL DEFAULT 'NONE',
    remediation_at  TIMESTAMPTZ,
    remediation_log TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    notes           TEXT,
    worker_id       TEXT,
    lock_until      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_repair_files_scan_state  ON repair_files(scan_state);
CREATE INDEX IF NOT EXISTS idx_repair_files_remediation ON repair_files(remediation);
CREATE INDEX IF NOT EXISTS idx_repair_files_lock_until  ON repair_files(lock_until);
CREATE TABLE IF NOT EXISTS repair_runs (
    id             SERIAL PRIMARY KEY,
    kind           TEXT    NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    folders_total  INTEGER NOT NULL DEFAULT 0,
    folders_done   INTEGER NOT NULL DEFAULT 0,
    clean_count    INTEGER NOT NULL DEFAULT 0,
    corrupt_count  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    empty_count    INTEGER NOT NULL DEFAULT 0,
    args_json      TEXT,
    notes          TEXT,
    worker_id      TEXT
);
"""


def _substitute_dsn_host(dsn: str, new_host: str) -> str:
    """Replace the host portion of a postgres DSN with new_host.

    Example: postgresql://user:pw@oldhost:5432/db  →  postgresql://user:pw@<new_host>:5432/db
    Lifted from Movie-Library-Compressor/tracker.py.
    """
    return re.sub(
        r"(@)([^:/]+)(:\d+)?(/)",
        lambda m: f"{m.group(1)}{new_host}{m.group(3) or ''}{m.group(4)}",
        dsn,
        count=1,
    )


def _init_postgres() -> RepairDBConnection:
    if not DATABASE_URL:
        raise ValueError(
            "DB_BACKEND='postgres' but DATABASE_URL is not set in .env. "
            "Add: DATABASE_URL=postgresql://user:pass@host:5432/dbname"
        )

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL backend. "
            "Install: pipenv install psycopg2-binary"
        )

    # Build candidate DSNs from POSTGRES_HOST_CANDIDATES; falls back to the
    # original DATABASE_URL when the list is empty.
    if POSTGRES_HOST_CANDIDATES:
        candidates = [_substitute_dsn_host(DATABASE_URL, h) for h in POSTGRES_HOST_CANDIDATES]
    else:
        candidates = [DATABASE_URL]

    last_error: Optional[Exception] = None
    raw = None
    for dsn in candidates:
        try:
            raw = psycopg2.connect(dsn, connect_timeout=5)
            break
        except Exception as exc:
            last_error = exc
            continue

    if raw is None:
        raise ConnectionError(
            f"Could not connect to PostgreSQL via any of {len(candidates)} host(s). "
            f"Last error: {last_error}"
        )

    # dict-style row access (so [r['folder_path']] works like sqlite3.Row)
    raw.cursor_factory = RealDictCursor

    with raw.cursor() as cur:
        cur.execute(_POSTGRES_SCHEMA)
    raw.commit()

    return RepairDBConnection("postgres", raw)


# =============================================================================
#  Internal helpers — table name + placeholder per backend
# =============================================================================

def _files_table(conn: RepairDBConnection) -> str:
    return "repair_files" if conn.backend == "postgres" else "files"


def _runs_table(conn: RepairDBConnection) -> str:
    return "repair_runs" if conn.backend == "postgres" else "runs"


def _ph(conn: RepairDBConnection) -> str:
    """Return the SQL parameter placeholder for the active backend."""
    return "%s" if conn.backend == "postgres" else "?"


def _now() -> str:
    """ISO8601 UTC timestamp string with explicit Z suffix.

    Both backends accept this format:
      - SQLite stores it as plain text
      - Postgres parses it into TIMESTAMPTZ as UTC (no ambiguous interpretation)
    """
    return datetime.utcnow().isoformat() + "Z"


def _row_to_dict(row, conn: RepairDBConnection) -> Dict[str, Any]:
    """Normalize a DB row to a plain dict regardless of backend."""
    if row is None:
        return None
    if conn.backend == "postgres":
        # RealDictCursor already returns a dict
        return dict(row)
    return dict(row)  # sqlite3.Row also supports dict()


def _execute(conn: RepairDBConnection, sql: str, params: tuple = ()):
    """Execute a query, abstracting cursor/commit semantics."""
    if conn.backend == "postgres":
        with conn.raw.cursor() as cur:
            cur.execute(sql, params)
        conn.raw.commit()
    else:
        conn.raw.execute(sql, params)
        conn.raw.commit()


def _fetchall(conn: RepairDBConnection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    if conn.backend == "postgres":
        with conn.raw.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    cur = conn.raw.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _fetchone(conn: RepairDBConnection, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    if conn.backend == "postgres":
        with conn.raw.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return dict(row) if row else None
    cur = conn.raw.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


# =============================================================================
#  CRUD operations — backend-agnostic
# =============================================================================

def upsert_file_record(conn: RepairDBConnection, record: Dict[str, Any]):
    """Insert or update a file record."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()

    existing = _fetchone(
        conn,
        f"SELECT id FROM {table} WHERE folder_path = {ph}",
        (record["folder_path"],),
    )

    if existing:
        sql = f"""
            UPDATE {table} SET
                video_path = {ph},
                size_bytes = {ph},
                duration_sec = {ph},
                scan_state = {ph},
                last_scan_at = {ph},
                last_scan_secs = {ph},
                stderr_tail = {ph},
                radarr_movie_id = {ph},
                radarr_tmdb_id = {ph}
            WHERE folder_path = {ph}
        """
        params = (
            record.get("video_path"),
            record.get("size_bytes", 0),
            record.get("duration_sec"),
            record.get("scan_state", "UNKNOWN"),
            record.get("last_scan_at", now),
            record.get("last_scan_secs"),
            record.get("stderr_tail"),
            record.get("radarr_movie_id"),
            record.get("radarr_tmdb_id"),
            record["folder_path"],
        )
    else:
        sql = f"""
            INSERT INTO {table} (
                folder_path, video_path, size_bytes, duration_sec,
                scan_state, last_scan_at, last_scan_secs, stderr_tail,
                radarr_movie_id, radarr_tmdb_id, first_seen_at
            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """
        params = (
            record["folder_path"],
            record.get("video_path"),
            record.get("size_bytes", 0),
            record.get("duration_sec"),
            record.get("scan_state", "UNKNOWN"),
            record.get("last_scan_at", now),
            record.get("last_scan_secs"),
            record.get("stderr_tail"),
            record.get("radarr_movie_id"),
            record.get("radarr_tmdb_id"),
            now,
        )

    _execute(conn, sql, params)


def get_files(
    conn: RepairDBConnection,
    filter_state: Optional[str] = None,
    filter_remediation: Optional[str] = None,
    sort_by: str = "size_bytes",
    order: str = "DESC",
) -> List[Dict[str, Any]]:
    """Retrieve file records with optional filtering and sorting."""
    table = _files_table(conn)
    ph = _ph(conn)

    # Whitelist sort_by / order to prevent SQL injection
    allowed_sorts = {
        "size_bytes", "folder_path", "scan_state", "remediation",
        "last_scan_at", "first_seen_at", "attempts",
    }
    if sort_by not in allowed_sorts:
        sort_by = "size_bytes"
    order = "ASC" if str(order).upper() == "ASC" else "DESC"

    query = f"SELECT * FROM {table} WHERE 1=1"
    params: list = []

    if filter_state:
        query += f" AND scan_state = {ph}"
        params.append(filter_state)
    if filter_remediation:
        query += f" AND remediation = {ph}"
        params.append(filter_remediation)

    query += f" ORDER BY {sort_by} {order}"

    return _fetchall(conn, query, tuple(params))


def record_run_start(conn: RepairDBConnection, kind: str, args: Dict[str, Any]) -> int:
    """Record the start of a scan or remediate run. Returns run_id."""
    table = _runs_table(conn)
    ph = _ph(conn)
    now = _now()

    if conn.backend == "postgres":
        with conn.raw.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} (kind, started_at, args_json) "
                f"VALUES ({ph}, {ph}, {ph}) RETURNING id",
                (kind, now, json.dumps(args)),
            )
            run_id = cur.fetchone()["id"]
        conn.raw.commit()
        return run_id

    # sqlite path
    cur = conn.raw.execute(
        f"INSERT INTO {table} (kind, started_at, args_json) VALUES ({ph}, {ph}, {ph})",
        (kind, now, json.dumps(args)),
    )
    conn.raw.commit()
    return cur.lastrowid


def record_run_finish(conn: RepairDBConnection, run_id: int, stats: Dict[str, Any]):
    """Update run record with completion stats."""
    table = _runs_table(conn)
    ph = _ph(conn)
    now = _now()

    sql = f"""
        UPDATE {table} SET
            finished_at = {ph},
            folders_total = {ph},
            folders_done = {ph},
            clean_count = {ph},
            corrupt_count = {ph},
            error_count = {ph},
            empty_count = {ph}
        WHERE id = {ph}
    """
    params = (
        now,
        stats.get("folders_total", 0),
        stats.get("folders_done", 0),
        stats.get("clean_count", 0),
        stats.get("corrupt_count", 0),
        stats.get("error_count", 0),
        stats.get("empty_count", 0),
        run_id,
    )
    _execute(conn, sql, params)


# =============================================================================
#  Remediation state mutators
# =============================================================================

def mark_queued(conn: RepairDBConnection, folder_paths: List[str]):
    """Mark files as QUEUED for remediation."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    for path in folder_paths:
        _execute(
            conn,
            f"UPDATE {table} SET remediation = 'QUEUED', remediation_at = {ph} "
            f"WHERE folder_path = {ph}",
            (now, path),
        )


def mark_deleted(conn: RepairDBConnection, folder_path: str):
    """Mark file as DELETED (and increment attempts)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'DELETED', remediation_at = {ph}, "
        f"attempts = attempts + 1 WHERE folder_path = {ph}",
        (now, folder_path),
    )


def mark_researching(conn: RepairDBConnection, folder_path: str):
    """Mark file as RESEARCHING (Radarr search triggered)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'RESEARCHING', remediation_at = {ph} "
        f"WHERE folder_path = {ph}",
        (now, folder_path),
    )


def mark_remediated(conn: RepairDBConnection, folder_path: str):
    """Mark file as REMEDIATED (scan verified clean after re-acquisition)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'REMEDIATED', remediation_at = {ph} "
        f"WHERE folder_path = {ph}",
        (now, folder_path),
    )


def mark_failed(conn: RepairDBConnection, folder_path: str, error_msg: str):
    """Mark file as FAILED with error message."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'FAILED', remediation_at = {ph}, "
        f"remediation_log = {ph} WHERE folder_path = {ph}",
        (now, error_msg, folder_path),
    )


def mark_skipped(conn: RepairDBConnection, folder_path: str):
    """Mark file as SKIPPED (user doesn't want to remediate)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'SKIPPED', remediation_at = {ph} "
        f"WHERE folder_path = {ph}",
        (now, folder_path),
    )


def mark_none(conn: RepairDBConnection, folder_path: str):
    """Reset remediation state to NONE (remove from queue, undo skip, etc.)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET remediation = 'NONE', remediation_at = {ph} "
        f"WHERE folder_path = {ph}",
        (now, folder_path),
    )


def mark_missing(conn: RepairDBConnection, folder_path: str):
    """Mark a folder as MISSING (no longer exists on disk)."""
    table = _files_table(conn)
    ph = _ph(conn)
    now = _now()
    _execute(
        conn,
        f"UPDATE {table} SET scan_state = 'MISSING', last_scan_at = {ph} "
        f"WHERE folder_path = {ph}",
        (now, folder_path),
    )


def delete_record(conn: RepairDBConnection, folder_path: str):
    """Permanently delete a file record from the database."""
    table = _files_table(conn)
    ph = _ph(conn)
    _execute(conn, f"DELETE FROM {table} WHERE folder_path = {ph}", (folder_path,))


def verify_existence(conn: RepairDBConnection, paths: Optional[List[str]] = None) -> int:
    """Check if folders still exist on disk and mark missing ones.

    If paths is None, check all non-MISSING records.
    Returns number of newly-missing folders detected.
    """
    table = _files_table(conn)

    if paths is None:
        rows = _fetchall(conn, f"SELECT folder_path FROM {table} WHERE scan_state != 'MISSING'")
        paths = [r["folder_path"] for r in rows]

    missing_count = 0
    for path in paths:
        try:
            if not Path(path).exists():
                mark_missing(conn, path)
                missing_count += 1
        except (OSError, PermissionError):
            # Network/permission issues — don't mark missing
            pass

    return missing_count


# =============================================================================
#  Distributed scan coordination — claim/release locks for multi-PC scanning
# =============================================================================

def claim_for_scan(
    conn: RepairDBConnection,
    folder_path: str,
    worker_id: str,
    lock_minutes: int = 60,
) -> bool:
    """Atomically claim a folder for scanning.

    Returns True if claim was acquired (this worker can proceed to scan).
    Returns False if another worker already holds an active lock.

    Behavior:
      - If the folder doesn't exist in the database yet, INSERT it with the
        claim in place.
      - If it exists and has no active lock (or lock has expired), UPDATE
        with our worker_id and a fresh lock_until.
      - If another worker holds an unexpired lock, return False.

    The lock auto-expires after `lock_minutes` so dead workers don't block
    folders forever.
    """
    table = _files_table(conn)
    ph = _ph(conn)
    now = datetime.utcnow()
    now_iso = now.isoformat() + "Z"
    expiry_iso = (now + timedelta(minutes=lock_minutes)).isoformat() + "Z"

    if conn.backend == "postgres":
        # Postgres: UPSERT with conditional WHERE on the UPDATE path.
        # The UPDATE proceeds only when:
        #   - no lock is held, OR
        #   - the existing lock has expired, OR
        #   - we already own the lock (refresh case)
        # If none of those are true (different worker holds active lock), the
        # ON CONFLICT WHERE filter is false and the row is NOT updated, leaving
        # RETURNING to yield no row (we lost the claim).
        sql = f"""
            INSERT INTO {table} (folder_path, worker_id, lock_until,
                                 scan_state, first_seen_at)
            VALUES ({ph}, {ph}, {ph}::timestamptz, 'SCANNING', {ph}::timestamptz)
            ON CONFLICT (folder_path) DO UPDATE
                SET worker_id  = EXCLUDED.worker_id,
                    lock_until = EXCLUDED.lock_until,
                    scan_state = 'SCANNING'
                WHERE {table}.lock_until IS NULL
                   OR {table}.lock_until < NOW()
                   OR {table}.worker_id = EXCLUDED.worker_id
            RETURNING worker_id, scan_state
        """
        with conn.raw.cursor() as cur:
            cur.execute(sql, (folder_path, worker_id, expiry_iso, now_iso))
            row = cur.fetchone()
        conn.raw.commit()
        if row is None:
            # Another worker holds the lock — INSERT did nothing on conflict
            return False
        # row is a RealDictRow; check that we are the holder
        return row.get("worker_id") == worker_id

    # SQLite path: simpler since there's only one process at a time in practice.
    # We still respect the lock semantics for symmetry.
    existing = _fetchone(
        conn,
        f"SELECT worker_id, lock_until FROM {table} WHERE folder_path = {ph}",
        (folder_path,),
    )

    if existing is None:
        # New folder — insert with the claim
        _execute(
            conn,
            f"""INSERT INTO {table}
                (folder_path, worker_id, lock_until, scan_state, first_seen_at)
                VALUES ({ph}, {ph}, {ph}, 'SCANNING', {ph})""",
            (folder_path, worker_id, expiry_iso, now_iso),
        )
        return True

    # Folder exists — check if locked by someone else
    other_lock = existing.get("lock_until")
    if other_lock and other_lock > now_iso and existing.get("worker_id") != worker_id:
        # Active lock by another worker
        return False

    # Either expired lock or no lock or we are the same worker — claim it
    _execute(
        conn,
        f"""UPDATE {table}
            SET worker_id = {ph}, lock_until = {ph}, scan_state = 'SCANNING'
            WHERE folder_path = {ph}""",
        (worker_id, expiry_iso, folder_path),
    )
    return True


def release_scan_claim(conn: RepairDBConnection, folder_path: str):
    """Release the scan lock on a folder (after scanning is done).

    Clears worker_id and lock_until. Does NOT touch scan_state — the caller
    sets that to the actual result (CLEAN / CORRUPT / etc) via upsert.
    """
    table = _files_table(conn)
    ph = _ph(conn)
    _execute(
        conn,
        f"UPDATE {table} SET worker_id = NULL, lock_until = NULL "
        f"WHERE folder_path = {ph}",
        (folder_path,),
    )


def get_locked_folders(conn: RepairDBConnection) -> List[Dict[str, Any]]:
    """Return folders currently locked by some worker (for status display)."""
    table = _files_table(conn)
    if conn.backend == "postgres":
        sql = f"""
            SELECT folder_path, worker_id, lock_until
            FROM {table}
            WHERE lock_until IS NOT NULL AND lock_until > NOW()
            ORDER BY lock_until DESC
        """
        return _fetchall(conn, sql)
    # SQLite
    now_iso = datetime.utcnow().isoformat() + "Z"
    sql = f"""
        SELECT folder_path, worker_id, lock_until
        FROM {table}
        WHERE lock_until IS NOT NULL AND lock_until > ?
        ORDER BY lock_until DESC
    """
    return _fetchall(conn, sql, (now_iso,))
