"""Safe, timestamped backups of the SQLite database.

Backs up config.DB_PATH to config.DB_BACKUP_DIR (default: the Z: deploy share)
using SQLite's online .backup API, so the copy is consistent even if a WAL is
active. Keeps the most recent config.DB_BACKUP_KEEP copies and prunes the rest.

Everything is best-effort: any failure (share offline, permissions, etc.) is
swallowed and reported via the return value, never raised — a backup problem
must not crash the app or block exit.
"""
import sqlite3
import time
from pathlib import Path

import config


def _backup_dir() -> Path:
    return Path(config.DB_BACKUP_DIR)


def backup_db() -> dict:
    """Create one timestamped backup of the SQLite DB.

    Returns a dict: {ok: bool, path: str|None, error: str|None, pruned: int}.
    No-ops (ok=False) for the Postgres backend or when backups are disabled.
    """
    result = {"ok": False, "path": None, "error": None, "pruned": 0}

    # Only meaningful for SQLite.
    if getattr(config, "DB_BACKEND", "sqlite") != "sqlite":
        result["error"] = "not sqlite backend"
        return result

    dest_dir_str = (config.DB_BACKUP_DIR or "").strip()
    if not dest_dir_str:
        result["error"] = "backups disabled (DB_BACKUP_DIR empty)"
        return result

    src = Path(config.DB_PATH)
    if not src.exists():
        result["error"] = f"source DB not found: {src}"
        return result

    dest_dir = Path(dest_dir_str)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        result["error"] = f"cannot create backup dir {dest_dir}: {e}"
        return result

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"repair-{stamp}.db"

    # Online backup: consistent snapshot without needing the app to be idle.
    src_conn = dst_conn = None
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dest))
        with dst_conn:
            src_conn.backup(dst_conn)
        result["ok"] = True
        result["path"] = str(dest)
    except Exception as e:
        result["error"] = f"backup failed: {e}"
        # Clean up a partial file if one was created.
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return result
    finally:
        for c in (dst_conn, src_conn):
            try:
                if c:
                    c.close()
            except Exception:
                pass

    # Prune old backups, keeping the newest N.
    try:
        keep = int(getattr(config, "DB_BACKUP_KEEP", 30))
        backups = sorted(
            dest_dir.glob("repair-*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep:]:
            try:
                old.unlink()
                result["pruned"] += 1
            except Exception:
                pass
    except Exception:
        pass

    return result
