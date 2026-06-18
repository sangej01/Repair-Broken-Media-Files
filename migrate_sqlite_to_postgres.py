"""Migrate data from SQLite (repair.db) to PostgreSQL (repair_files/repair_runs).

Safe to run multiple times: uses upsert semantics, idempotent.

Usage:
  pipenv run python migrate_sqlite_to_postgres.py            # dry-run by default
  pipenv run python migrate_sqlite_to_postgres.py --execute  # actually copy
  pipenv run python migrate_sqlite_to_postgres.py --execute --replace
                                                              # replaces conflicting rows in Postgres

What it does:
  1. Reads every row from SQLite `files` and `runs` tables
  2. Inserts each into Postgres `repair_files` / `repair_runs`
  3. By default, skips rows that already exist in Postgres (UPSERT-conflict)
  4. With --replace, overwrites Postgres rows where folder_path matches

Safety:
  - Does NOT touch the SQLite database (read-only on source)
  - Does NOT delete anything from Postgres
  - Dry-run by default — must pass --execute to actually write
  - Reports counts at the end so you can verify
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from config import DATABASE_URL, DB_PATH, POSTGRES_HOST_CANDIDATES


def _substitute_dsn_host(dsn: str, new_host: str) -> str:
    import re
    return re.sub(
        r"(@)([^:/]+)(:\d+)?(/)",
        lambda m: f"{m.group(1)}{new_host}{m.group(3) or ''}{m.group(4)}",
        dsn,
        count=1,
    )


def connect_postgres():
    """Connect to Postgres trying each candidate host."""
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pipenv install psycopg2-binary", file=sys.stderr)
        sys.exit(1)

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set in .env", file=sys.stderr)
        sys.exit(1)

    candidates = (
        [_substitute_dsn_host(DATABASE_URL, h) for h in POSTGRES_HOST_CANDIDATES]
        if POSTGRES_HOST_CANDIDATES else [DATABASE_URL]
    )

    last_err = None
    for dsn in candidates:
        try:
            conn = psycopg2.connect(dsn, connect_timeout=5)
            return conn
        except Exception as e:
            last_err = e
    print(f"ERROR: could not connect to Postgres. Last error: {last_err}", file=sys.stderr)
    sys.exit(1)


def ensure_postgres_schema(pg_conn):
    """Make sure repair_files / repair_runs tables exist."""
    schema = """
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
    with pg_conn.cursor() as cur:
        cur.execute(schema)
    pg_conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true",
                    help="Actually perform the migration (default: dry-run)")
    ap.add_argument("--replace", action="store_true",
                    help="Replace existing Postgres rows on conflict (default: skip)")
    ap.add_argument("--source", type=Path, default=DB_PATH,
                    help=f"SQLite source DB path (default: {DB_PATH})")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"ERROR: SQLite source not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    conflict = "REPLACE" if args.replace else "SKIP"
    print(f"=== SQLite -> Postgres migration ({mode}, conflicts: {conflict}) ===")
    print(f"Source:  {args.source}")
    print(f"Target:  {DATABASE_URL}")
    print(f"Host fallback: {POSTGRES_HOST_CANDIDATES}")
    print()

    # Open SQLite (read-only)
    sql_conn = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    sql_conn.row_factory = sqlite3.Row

    # Count source data
    n_files_src = sql_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_runs_src = sql_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"SQLite has {n_files_src} files, {n_runs_src} runs")

    # Connect Postgres + ensure schema
    pg_conn = connect_postgres()
    ensure_postgres_schema(pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM repair_files")
        n_files_dst = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM repair_runs")
        n_runs_dst = cur.fetchone()[0]
    print(f"Postgres has {n_files_dst} files, {n_runs_dst} runs (before migration)")
    print()

    # Build INSERT statement
    if args.replace:
        files_sql = """
            INSERT INTO repair_files (
                folder_path, video_path, size_bytes, duration_sec,
                scan_state, last_scan_at, last_scan_secs, stderr_tail,
                radarr_movie_id, radarr_tmdb_id,
                remediation, remediation_at, remediation_log,
                attempts, first_seen_at, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (folder_path) DO UPDATE SET
                video_path = EXCLUDED.video_path,
                size_bytes = EXCLUDED.size_bytes,
                duration_sec = EXCLUDED.duration_sec,
                scan_state = EXCLUDED.scan_state,
                last_scan_at = EXCLUDED.last_scan_at,
                last_scan_secs = EXCLUDED.last_scan_secs,
                stderr_tail = EXCLUDED.stderr_tail,
                radarr_movie_id = EXCLUDED.radarr_movie_id,
                radarr_tmdb_id = EXCLUDED.radarr_tmdb_id,
                remediation = EXCLUDED.remediation,
                remediation_at = EXCLUDED.remediation_at,
                remediation_log = EXCLUDED.remediation_log,
                attempts = EXCLUDED.attempts,
                notes = EXCLUDED.notes
        """
    else:
        files_sql = """
            INSERT INTO repair_files (
                folder_path, video_path, size_bytes, duration_sec,
                scan_state, last_scan_at, last_scan_secs, stderr_tail,
                radarr_movie_id, radarr_tmdb_id,
                remediation, remediation_at, remediation_log,
                attempts, first_seen_at, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (folder_path) DO NOTHING
        """

    runs_sql = """
        INSERT INTO repair_runs (
            kind, started_at, finished_at,
            folders_total, folders_done,
            clean_count, corrupt_count, error_count, empty_count,
            args_json, notes
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """

    # Migrate files
    files_inserted = 0
    files_skipped = 0
    print("Processing files table...")
    rows = sql_conn.execute("SELECT * FROM files").fetchall()
    for r in rows:
        params = (
            r["folder_path"],
            r["video_path"],
            r["size_bytes"] or 0,
            r["duration_sec"],
            r["scan_state"],
            r["last_scan_at"],
            r["last_scan_secs"],
            r["stderr_tail"],
            r["radarr_movie_id"],
            r["radarr_tmdb_id"],
            r["remediation"],
            r["remediation_at"],
            r["remediation_log"],
            r["attempts"] or 0,
            r["first_seen_at"],
            r["notes"],
        )
        if args.execute:
            with pg_conn.cursor() as cur:
                cur.execute(files_sql, params)
                if cur.rowcount > 0:
                    files_inserted += 1
                else:
                    files_skipped += 1
        else:
            print(f"  [DRY-RUN] would insert: {r['folder_path']} ({r['scan_state']})")
            files_inserted += 1  # for the summary

    if args.execute:
        pg_conn.commit()

    # Migrate runs (always insert, no UPSERT — runs are append-only audit records)
    runs_inserted = 0
    print("\nProcessing runs table...")
    rows = sql_conn.execute("SELECT * FROM runs").fetchall()
    for r in rows:
        params = (
            r["kind"],
            r["started_at"],
            r["finished_at"],
            r["folders_total"] or 0,
            r["folders_done"] or 0,
            r["clean_count"] or 0,
            r["corrupt_count"] or 0,
            r["error_count"] or 0,
            r["empty_count"] or 0,
            r["args_json"],
            r["notes"],
        )
        if args.execute:
            with pg_conn.cursor() as cur:
                cur.execute(runs_sql, params)
            runs_inserted += 1
        else:
            print(f"  [DRY-RUN] would insert run: {r['kind']} @ {r['started_at']}")
            runs_inserted += 1

    if args.execute:
        pg_conn.commit()

    # Final counts in Postgres
    if args.execute:
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM repair_files")
            n_files_after = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM repair_runs")
            n_runs_after = cur.fetchone()[0]
    else:
        n_files_after = n_files_dst
        n_runs_after = n_runs_dst

    sql_conn.close()
    pg_conn.close()

    # Summary
    print()
    print("=" * 60)
    print(f"SUMMARY ({mode})")
    print("=" * 60)
    print(f"  Files: inserted={files_inserted}, skipped={files_skipped}")
    print(f"  Runs:  inserted={runs_inserted}")
    if args.execute:
        print()
        print(f"  Postgres before: {n_files_dst} files, {n_runs_dst} runs")
        print(f"  Postgres after:  {n_files_after} files, {n_runs_after} runs")
        print()
        print("Migration complete!")
    else:
        print()
        print("This was a DRY-RUN. Re-run with --execute to actually copy data.")


if __name__ == "__main__":
    main()
