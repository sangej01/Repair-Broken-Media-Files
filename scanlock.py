"""Cross-process scan lock.

Prevents two scanners (e.g. the GUI and a CLI `scan`) from writing the same
SQLite database at once, which can lock/hang/crash the GUI. Uses a small PID
lock file next to the database.

Only meaningful for the SQLite backend (single-writer). For Postgres, which
handles concurrency itself, acquisition is a no-op that always succeeds.

Usage:
    import scanlock
    info = scanlock.acquire("gui")        # or "cli"
    if info is None:
        holder = scanlock.read_holder()   # dict or None -> tell the user who holds it
        ...refuse or warn...
    else:
        try:
            ...run scan...
        finally:
            scanlock.release()
"""
import json
import os
import time
from pathlib import Path

import config

# The lock file lives next to the SQLite DB. For Postgres we still use a local
# file path but never actually block (see _sqlite_backend()).
_LOCK_PATH = Path(str(config.DB_PATH) + ".scanlock")

# Track whether *this* process currently holds the lock, so release() is safe.
_held = False


def _sqlite_backend() -> bool:
    return getattr(config, "DB_BACKEND", "sqlite") == "sqlite"


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    if pid <= 0:
        return False
    try:
        # Windows-friendly liveness check without extra deps.
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True  # couldn't read exit code; assume alive to be safe
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        # Non-Windows / unexpected: fall back to os.kill(0) semantics.
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True  # unknown -> assume alive (safer: don't steal a live lock)


def read_holder():
    """Return the current lock holder as a dict, or None if unlocked/stale.

    A lock held by a dead PID is considered stale (returns None), so callers
    can safely take it over.
    """
    if not _sqlite_backend():
        return None
    try:
        data = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    pid = int(data.get("pid", 0) or 0)
    if not _pid_alive(pid):
        return None  # stale
    return data


def acquire(kind: str = "scan"):
    """Try to acquire the scan lock.

    Returns a dict describing our lock on success, or None if another LIVE
    process already holds it. A stale lock (dead PID) is taken over.
    """
    global _held
    if not _sqlite_backend():
        _held = True
        return {"pid": os.getpid(), "kind": kind, "backend": "postgres"}

    holder = read_holder()
    if holder is not None and int(holder.get("pid", 0)) != os.getpid():
        return None  # someone else alive holds it

    info = {
        "pid": os.getpid(),
        "kind": kind,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": os.environ.get("COMPUTERNAME", ""),
    }
    try:
        _LOCK_PATH.write_text(json.dumps(info), encoding="utf-8")
        _held = True
        return info
    except OSError:
        # If we can't write the lock, don't hard-fail the scan; proceed unlocked.
        _held = True
        return info


def release():
    """Release the lock if this process holds it."""
    global _held
    if not _held:
        return
    _held = False
    if not _sqlite_backend():
        return
    try:
        holder = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
        if int(holder.get("pid", 0)) == os.getpid():
            _LOCK_PATH.unlink(missing_ok=True)
    except (FileNotFoundError, ValueError, OSError):
        pass


def holder_description() -> str:
    """Human-readable description of the current holder (for messages)."""
    h = read_holder()
    if not h:
        return "another process"
    kind = h.get("kind", "scan")
    pid = h.get("pid", "?")
    started = h.get("started_at", "")
    return f"a {kind} scan (PID {pid}{', started ' + started if started else ''})"
