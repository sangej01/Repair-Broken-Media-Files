"""mount_drives.py -- Reusable Windows network drive self-healing.

CANONICAL SOURCE for the whole Media Tools Consortium. Edit only this copy
(Media Tools Consortium/common/mount_drives.py). Each consuming tool keeps
its own local copy (e.g. Pluck Movies/pipeline/mount_drives.py,
Movie Library Compressor/src/compressor/mount_drives.py) so imports and
PyInstaller specs don't need to change -- build-launcher.ps1 overwrites
those local copies from this file before building, so they can't drift.
If you're editing a local copy instead of this one, stop -- your edit will
be silently clobbered on the next build.

This module is intentionally self-contained: it has NO imports from the rest
of any tool's codebase.  You can drop it into any Python project and
call ``ensure_drives()`` with a list of drive specs.

Public API
----------
DriveSpec   -- typed dict describing one drive mapping
MountResult -- result of one mount attempt
ensure_drives(specs, *, logger=None, remount_delay=3.0) -> list[MountResult]
    For each spec whose drive letter is not currently accessible, attempt to
    (re)mount it via ``net use``.  Returns one MountResult per spec.

Typical usage (from a preflight check)::

    from pipeline.mount_drives import ensure_drives
    from config import MAPPED_DRIVES          # list[DriveSpec] from config

    results = ensure_drives(MAPPED_DRIVES, logger=logging.getLogger(__name__))
    for r in results:
        if not r["ok"]:
            print(f"Could not mount {r['letter']}: {r['message']}")

DriveSpec keys
--------------
letter      str     Single drive letter, e.g. "W"  (case-insensitive)
unc         str     UNC path, e.g. "\\\\192.168.1.198\\Media2"
                    or "\\\\sshfs\\sangej01@lime.whatbox.ca"
user        str     Username for ``net use /user:``
password    str     Password for ``net use``

All four keys are required.  The module never reads from os.environ or any
config file -- all credentials are supplied by the caller.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import TypedDict

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class DriveSpec(TypedDict):
    """One network drive mapping."""
    letter:   str   # e.g. "W"
    unc:      str   # e.g. r"\\192.168.1.198\Media2"
    user:     str
    password: str


class MountResult(TypedDict):
    """Result of one ensure_drives() attempt for a single drive."""
    letter:       str    # drive letter that was checked / mounted
    was_missing:  bool   # True  = drive was not accessible before we tried
    attempted:    bool   # True  = we ran net use (only when was_missing=True)
    ok:           bool   # True  = drive is accessible after our attempt
    message:      str    # human-readable summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _drive_accessible(letter: str) -> bool:
    """Return True if <letter>:\\ exists and is listable within ~5 s."""
    import os
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FT

    root = f"{letter.upper()}:\\"

    def _check():
        try:
            return os.path.isdir(root) and bool(os.listdir(root) or True)
        except OSError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_check).result(timeout=5)
    except _FT:
        return False


def _net_use_delete(letter: str) -> None:
    """Silently delete any stale mapping for *letter* before remounting."""
    try:
        subprocess.run(
            ["net", "use", f"{letter.upper()}:", "/delete", "/y"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _net_use_mount(spec: DriveSpec) -> tuple[bool, str]:
    """Run ``net use`` to mount one drive.  Returns (success, message)."""
    letter = spec["letter"].upper()
    unc    = spec["unc"]
    user   = spec["user"]
    pwd    = spec["password"]

    try:
        result = subprocess.run(
            [
                "net", "use",
                f"{letter}:",
                unc,
                pwd,
                f"/user:{user}",
                "/persistent:yes",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, f"Mounted {letter}: -> {unc}"
        stderr = (result.stderr or result.stdout or "").strip()
        msg = f"net use exited {result.returncode}: {stderr[:200]}"
        if "85" in stderr and unc.lower().startswith(r"\\sshfs"):
            # The sshfs process behind the letter is hung; it belongs to the WinFsp service,
            # so only an admin can stop it -- this app can't clear the letter itself.
            msg += (f"\n\n{letter}: is held by a hung SSHFS session. Fix: run "
                    r"C:\Scripts\Fix-WhatboxDrive.ps1 (one UAC prompt), then try again.")
        return False, msg
    except subprocess.TimeoutExpired:
        return False, f"net use timed out mounting {letter}: -> {unc}"
    except FileNotFoundError:
        return False, "net use not found -- is this Windows?"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ensure_drives(
    specs: list[DriveSpec],
    *,
    logger: logging.Logger | None = None,
    remount_delay: float = 3.0,
) -> list[MountResult]:
    """Ensure every drive in *specs* is accessible, mounting missing ones.

    Parameters
    ----------
    specs:
        List of DriveSpec dicts, typically ``config.MAPPED_DRIVES``.
    logger:
        Optional logger.  Falls back to the module-level ``_log``.
    remount_delay:
        Seconds to wait after a successful ``net use`` before re-testing
        accessibility.  SMB/SSHFS shares occasionally need a moment to
        settle before the root dir is listable.

    Returns
    -------
    list[MountResult]
        One entry per spec, in the same order.
    """
    log = logger or _log
    results: list[MountResult] = []

    for spec in specs:
        letter = spec["letter"].upper()

        if _drive_accessible(letter):
            log.debug("mount_drives: %s: is accessible, skipping.", letter)
            results.append(MountResult(
                letter=letter,
                was_missing=False,
                attempted=False,
                ok=True,
                message=f"{letter}: is already mounted and accessible.",
            ))
            continue

        # Drive is missing -- attempt to (re)mount
        log.warning("mount_drives: %s: not accessible, attempting mount -> %s",
                    letter, spec["unc"])

        _net_use_delete(letter)          # clear any stale ghost mapping first
        ok, msg = _net_use_mount(spec)

        if ok and remount_delay > 0:
            log.debug("mount_drives: %s: net use succeeded, waiting %.1fs for share to settle.",
                      letter, remount_delay)
            time.sleep(remount_delay)
            # Re-verify: net use can succeed but the share is still not listable
            # (e.g. SSHFS-Win key-auth fails silently).
            ok = _drive_accessible(letter)
            if not ok:
                msg = (f"net use reported success but {letter}: is still not "
                       f"accessible after {remount_delay:.0f}s -- "
                       f"check credentials or SSHFS-Win.")

        level = logging.INFO if ok else logging.ERROR
        log.log(level, "mount_drives: %s: %s", letter, msg)

        results.append(MountResult(
            letter=letter,
            was_missing=True,
            attempted=True,
            ok=ok,
            message=msg,
        ))

    return results
