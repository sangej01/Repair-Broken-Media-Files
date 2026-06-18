"""SQLite database interface for repair.db."""
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import json

from config import DB_PATH


def init_db() -> sqlite3.Connection:
    """Initialize the database schema and return connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    
    # Create files table
    conn.execute("""
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
            notes           TEXT
        )
    """)
    
    # Create indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_scan_state ON files(scan_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_remediation ON files(remediation)")
    
    # Create runs table
    conn.execute("""
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
        )
    """)
    
    conn.commit()
    return conn


def upsert_file_record(conn: sqlite3.Connection, record: Dict[str, Any]):
    """Insert or update a file record."""
    now = datetime.utcnow().isoformat()
    
    # Check if record exists
    existing = conn.execute(
        "SELECT id, first_seen_at FROM files WHERE folder_path = ?",
        (record["folder_path"],)
    ).fetchone()
    
    if existing:
        # Update existing record
        conn.execute("""
            UPDATE files SET
                video_path = ?,
                size_bytes = ?,
                duration_sec = ?,
                scan_state = ?,
                last_scan_at = ?,
                last_scan_secs = ?,
                stderr_tail = ?,
                radarr_movie_id = ?,
                radarr_tmdb_id = ?
            WHERE folder_path = ?
        """, (
            record.get("video_path"),
            record.get("size_bytes", 0),
            record.get("duration_sec"),
            record.get("scan_state", "UNKNOWN"),
            record.get("last_scan_at", now),
            record.get("last_scan_secs"),
            record.get("stderr_tail"),
            record.get("radarr_movie_id"),
            record.get("radarr_tmdb_id"),
            record["folder_path"]
        ))
    else:
        # Insert new record
        conn.execute("""
            INSERT INTO files (
                folder_path, video_path, size_bytes, duration_sec,
                scan_state, last_scan_at, last_scan_secs, stderr_tail,
                radarr_movie_id, radarr_tmdb_id, first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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
            now
        ))
    
    conn.commit()


def get_files(conn: sqlite3.Connection, filter_state: Optional[str] = None,
              filter_remediation: Optional[str] = None,
              sort_by: str = "size_bytes", order: str = "DESC") -> List[Dict[str, Any]]:
    """Retrieve file records with optional filtering and sorting."""
    query = "SELECT * FROM files WHERE 1=1"
    params = []
    
    if filter_state:
        query += " AND scan_state = ?"
        params.append(filter_state)
    
    if filter_remediation:
        query += " AND remediation = ?"
        params.append(filter_remediation)
    
    query += f" ORDER BY {sort_by} {order}"
    
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def record_run_start(conn: sqlite3.Connection, kind: str, args: Dict[str, Any]) -> int:
    """Record the start of a scan or remediate run. Returns run_id."""
    now = datetime.utcnow().isoformat()
    cursor = conn.execute("""
        INSERT INTO runs (kind, started_at, args_json)
        VALUES (?, ?, ?)
    """, (kind, now, json.dumps(args)))
    conn.commit()
    return cursor.lastrowid


def record_run_finish(conn: sqlite3.Connection, run_id: int, stats: Dict[str, Any]):
    """Update run record with completion stats."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE runs SET
            finished_at = ?,
            folders_total = ?,
            folders_done = ?,
            clean_count = ?,
            corrupt_count = ?,
            error_count = ?,
            empty_count = ?
        WHERE id = ?
    """, (
        now,
        stats.get("folders_total", 0),
        stats.get("folders_done", 0),
        stats.get("clean_count", 0),
        stats.get("corrupt_count", 0),
        stats.get("error_count", 0),
        stats.get("empty_count", 0),
        run_id
    ))
    conn.commit()


def mark_queued(conn: sqlite3.Connection, folder_paths: List[str]):
    """Mark files as QUEUED for remediation."""
    now = datetime.utcnow().isoformat()
    for path in folder_paths:
        conn.execute("""
            UPDATE files SET remediation = 'QUEUED', remediation_at = ?
            WHERE folder_path = ?
        """, (now, path))
    conn.commit()


def mark_deleted(conn: sqlite3.Connection, folder_path: str):
    """Mark file as DELETED."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE files SET remediation = 'DELETED', remediation_at = ?, attempts = attempts + 1
        WHERE folder_path = ?
    """, (now, folder_path))
    conn.commit()


def mark_researching(conn: sqlite3.Connection, folder_path: str):
    """Mark file as RESEARCHING (Radarr search triggered)."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE files SET remediation = 'RESEARCHING', remediation_at = ?
        WHERE folder_path = ?
    """, (now, folder_path))
    conn.commit()


def mark_remediated(conn: sqlite3.Connection, folder_path: str):
    """Mark file as REMEDIATED (scan verified clean after re-acquisition)."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE files SET remediation = 'REMEDIATED', remediation_at = ?
        WHERE folder_path = ?
    """, (now, folder_path))
    conn.commit()


def mark_failed(conn: sqlite3.Connection, folder_path: str, error_msg: str):
    """Mark file as FAILED with error message."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE files SET remediation = 'FAILED', remediation_at = ?, remediation_log = ?
        WHERE folder_path = ?
    """, (now, error_msg, folder_path))
    conn.commit()


def mark_skipped(conn: sqlite3.Connection, folder_path: str):
    """Mark file as SKIPPED (user doesn't want to remediate)."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE files SET remediation = 'SKIPPED', remediation_at = ?
        WHERE folder_path = ?
    """, (now, folder_path))
    conn.commit()
