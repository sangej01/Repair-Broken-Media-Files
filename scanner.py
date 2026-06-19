"""Video file scanner - null-decode corruption detection."""
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple, Callable, List
from datetime import datetime, timedelta

import db


# Global tracking of active ffmpeg processes
_active_processes: list = []
_active_processes_lock = threading.Lock()


def _register_process(proc):
    """Register an ffmpeg process for tracking."""
    with _active_processes_lock:
        _active_processes.append(proc)


def _unregister_process(proc):
    """Unregister an ffmpeg process when it completes."""
    with _active_processes_lock:
        if proc in _active_processes:
            _active_processes.remove(proc)


def _kill_all_active_processes():
    """Kill all tracked ffmpeg processes immediately."""
    with _active_processes_lock:
        procs_to_kill = list(_active_processes)
        _active_processes.clear()
    
    for proc in procs_to_kill:
        try:
            # Try terminate first (SIGTERM equivalent)
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                # Force kill if terminate didn't work
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            # Last resort - taskkill by PID
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=3
                )
            except:
                pass


# Lifted from library_corruption_sweep.py and Pluck Movies pipeline/common.py
TROUBLE_KEYWORDS = (
    "file ended prematurely",
    "ended prematurely",
    "non monotonically",
    "non-monotonous",
    "decode_slice",
    "missing reference",
    "could not find codec parameters",
    "invalid as first byte of an ebml",
    "invalid nal unit size",
    "concealing",
    "corrupt",
    "truncated",
    "packet too large",
    "no frame",
)

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg"}
IGNORE_DIR_NAMES = {"Extras", "Sample", "Featurettes", "Behind The Scenes", "Trailers"}


def _kill_ffmpeg_processes():
    """Aggressively kill all ffmpeg processes using multiple methods."""
    # Method 1: taskkill (most reliable on Windows)
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "ffmpeg.exe"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=5
        )
    except Exception as e:
        print(f"taskkill failed: {e}", file=sys.stderr)
    
    # Method 2: psutil if available
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and 'ffmpeg' in proc.info['name'].lower():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        pass
    except Exception as e:
        print(f"psutil kill failed: {e}", file=sys.stderr)


def _is_video(p: Path) -> bool:
    """Check if path is a video file."""
    return p.suffix.lower() in VIDEO_EXTS


def largest_video_in_folder(folder: Path) -> Optional[Path]:
    """
    Find the largest video file in a folder.
    Mirrors Pluck Movies and Compressor behavior.
    """
    best: Optional[Path] = None
    best_size = -1
    
    for root, dirs, files in os.walk(folder):
        # Prune ignored subdirs in-place so os.walk doesn't recurse into them
        dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES]
        
        for fname in files:
            p = Path(root) / fname
            if not _is_video(p):
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > best_size:
                best_size = sz
                best = p
    
    return best


def _classify_stderr(stderr: str) -> str:
    """Check if stderr contains trouble keywords."""
    low = stderr.lower()
    for kw in TROUBLE_KEYWORDS:
        if kw in low:
            return "CORRUPT"
    return "CLEAN"


def null_decode(video_path: Path, timeout_sec: int = 1800, progress_callback=None) -> Tuple[str, str, float]:
    """
    Run ffmpeg null-decode to detect corruption.
    Returns: (scan_state, stderr_tail, elapsed_sec)
    scan_state is one of: CLEAN, CORRUPT, ERROR, TIMEOUT
    
    progress_callback: optional function(elapsed_sec) called periodically during scan
    """
    start = time.time()
    proc = None
    
    # Adjust timeout based on file size for large files
    # Rough estimate: 1 minute per GB minimum, with floor of timeout_sec
    try:
        file_size_gb = video_path.stat().st_size / (1024**3)
        # Allow 2 minutes per GB for slow NAS, with min of timeout_sec
        adaptive_timeout = max(timeout_sec, int(file_size_gb * 120))
        timeout_sec = adaptive_timeout
    except Exception:
        pass  # Use default if we can't stat
    
    try:
        # Start ffmpeg process without waiting
        # CREATE_NEW_PROCESS_GROUP allows us to kill it properly on Windows
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", str(video_path), "-f", "null", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        
        # Register for tracking so we can kill it from outside
        _register_process(proc)
        
        # Poll process and emit progress updates
        stderr_lines = []
        while proc.poll() is None:
            # Check if we've exceeded timeout
            elapsed = time.time() - start
            if elapsed > timeout_sec:
                # Kill the process properly
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except:
                    pass
                # Use TIMEOUT state (not ERROR) so users can rescan with longer timeout
                return "TIMEOUT", f"TIMEOUT after {timeout_sec}s (file may be too large for current timeout)", elapsed
            
            # Emit progress callback
            if progress_callback:
                progress_callback(elapsed)
            
            # Read stderr if available (non-blocking)
            try:
                import select
                if hasattr(select, 'select'):
                    # Unix-like
                    ready, _, _ = select.select([proc.stderr], [], [], 0.5)
                    if ready:
                        line = proc.stderr.readline()
                        if line:
                            stderr_lines.append(line)
                else:
                    # Windows - just sleep
                    time.sleep(0.5)
            except:
                # Fallback - just sleep
                time.sleep(0.5)
        
        # Process completed, get remaining output
        remaining_stderr, _ = proc.communicate()
        if remaining_stderr:
            stderr_lines.append(remaining_stderr)
        
        stderr_output = ''.join(stderr_lines)
        
    except FileNotFoundError:
        return "ERROR", "ffmpeg not found on PATH", 0.0
    except Exception as exc:
        # Ensure process is killed if exception occurs
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except:
                pass
        return "ERROR", f"exec failure: {exc}", time.time() - start
    finally:
        # Always unregister the process
        if proc:
            _unregister_process(proc)
    
    elapsed = time.time() - start
    stderr_tail = stderr_output[-400:].strip() if stderr_output else ""
    
    if proc.returncode != 0:
        # ffmpeg exited non-zero -- almost always corruption it couldn't push past
        return "CORRUPT", stderr_tail or f"ffmpeg exit {proc.returncode}", elapsed
    
    # Exit 0 but stderr might contain trouble keywords (the 28YL pattern)
    verdict = _classify_stderr(stderr_output or "")
    return verdict, stderr_tail, elapsed


def _enumerate_movie_folders(roots: list) -> list:
    """Return every immediate subfolder under each root."""
    out = []
    for root in roots:
        root_path = Path(root) if not isinstance(root, Path) else root
        if not root_path.exists():
            print(f"  [skip] root missing: {root_path}", file=sys.stderr)
            continue
        for sub in sorted(root_path.iterdir(), key=lambda p: p.name.lower()):
            if sub.is_dir():
                out.append(sub)
    return out


def scan_library(roots: list, workers: int, db_conn, progress_callback: Optional[Callable] = None,
                 file_progress_callback: Optional[Callable] = None,
                 scan_start_callback: Optional[Callable] = None,
                 cancel_flag: Optional[Callable] = None,
                 rescan: bool = False, limit: Optional[int] = None, timeout_sec: int = 1800,
                 folders: Optional[List[Path]] = None):
    """
    Scan library folders for corruption.
    - roots: list of Path objects (used to enumerate folders unless `folders` is given)
    - workers: concurrent ffmpeg workers
    - db_conn: sqlite3.Connection
    - progress_callback: optional function(current, total, folder_name, state) - called after each folder completes
    - file_progress_callback: optional function(folder_path, elapsed_sec) - called during each file scan
    - scan_start_callback: optional function(folder_path) - called when a folder scan starts
    - cancel_flag: optional function() -> bool - called to check if scan should be cancelled
    - rescan: if False, skip folders with recent last_scan_at (< 7 days)
    - limit: optional max folders to scan (for testing)
    - timeout_sec: per-file ffmpeg timeout
    - folders: optional explicit list of Path objects to scan instead of enumerating
               from `roots`. Useful for benchmarks or targeted re-scans.
    
    Returns: dict with scan stats (folders_total, folders_done, clean_count, corrupt_count, error_count, empty_count)
    """
    # Reclaim any locks held by THIS worker that may have leaked from a
    # crashed previous run, AND reset their scan_state from SCANNING back to
    # UNKNOWN so they get re-scanned. Other workers' active locks are left
    # alone — those expire automatically via lock_until.
    #
    # Also clean up SCANNING rows whose lock has already expired regardless
    # of which worker held them — the previous worker is clearly dead.
    try:
        from config import WORKER_ID
        if db_conn.backend == "postgres":
            with db_conn.raw.cursor() as cur:
                # Clean up our own leaked locks
                cur.execute(
                    "UPDATE repair_files "
                    "SET worker_id = NULL, lock_until = NULL, "
                    "    scan_state = CASE WHEN scan_state = 'SCANNING' THEN 'UNKNOWN' "
                    "                      ELSE scan_state END "
                    "WHERE worker_id = %s",
                    (WORKER_ID,),
                )
                # Clean up other workers' expired locks (they crashed/disappeared)
                cur.execute(
                    "UPDATE repair_files "
                    "SET worker_id = NULL, lock_until = NULL, "
                    "    scan_state = CASE WHEN scan_state = 'SCANNING' THEN 'UNKNOWN' "
                    "                      ELSE scan_state END "
                    "WHERE lock_until IS NOT NULL AND lock_until < NOW()"
                )
            db_conn.raw.commit()
        else:
            # datetime/timedelta are imported at module level (line 10)
            now_iso = datetime.utcnow().isoformat() + "Z"
            db_conn.raw.execute(
                "UPDATE files "
                "SET worker_id = NULL, lock_until = NULL, "
                "    scan_state = CASE WHEN scan_state = 'SCANNING' THEN 'UNKNOWN' "
                "                      ELSE scan_state END "
                "WHERE worker_id = ?",
                (WORKER_ID,),
            )
            db_conn.raw.execute(
                "UPDATE files "
                "SET worker_id = NULL, lock_until = NULL, "
                "    scan_state = CASE WHEN scan_state = 'SCANNING' THEN 'UNKNOWN' "
                "                      ELSE scan_state END "
                "WHERE lock_until IS NOT NULL AND lock_until < ?",
                (now_iso,),
            )
            db_conn.raw.commit()
    except Exception:
        pass

    # Enumerate all folders (or use the explicit list passed in)
    if folders is not None:
        all_folders = list(folders)
    else:
        all_folders = _enumerate_movie_folders(roots)
    total = len(all_folders)

    if progress_callback:
        progress_callback(0, total, "", "discovery")
    
    # Build the set of folders to skip:
    #   1. Recently scanned with a definitive result (CLEAN/CORRUPT/EMPTY/MISSING)
    #   2. Currently locked by another worker (multi-PC mode)
    skip_paths = set()

    if not rescan:
        cutoff_dt = datetime.utcnow() - timedelta(days=7)
        existing = db.get_files(db_conn)
        # Skip folder only if: scanned recently AND result was definitive (not TIMEOUT/ERROR)
        # last_scan_at can be either a string (SQLite ISO text) or a datetime (Postgres TIMESTAMPTZ),
        # so normalize both sides to datetime objects before comparing.
        for r in existing:
            last_scan = r.get("last_scan_at")
            if not last_scan:
                continue
            if r.get("scan_state") in ("TIMEOUT", "ERROR", "UNKNOWN", "SCANNING"):
                continue
            # Normalize to naive UTC datetime
            if isinstance(last_scan, str):
                try:
                    last_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                except ValueError:
                    continue
            else:
                last_dt = last_scan
            # Strip timezone info for comparison (cutoff_dt is naive UTC)
            if last_dt.tzinfo is not None:
                last_dt = last_dt.replace(tzinfo=None)
            if last_dt > cutoff_dt:
                skip_paths.add(r["folder_path"])

    # Always exclude folders currently locked by another worker (even with --rescan).
    # The atomic claim_for_scan() in _scan_one is the real safeguard, but pre-filtering
    # avoids spending time on folders we know we'd be denied.
    try:
        from config import WORKER_ID
        for r in db.get_locked_folders(db_conn):
            if r.get("worker_id") != WORKER_ID:
                skip_paths.add(r["folder_path"])
    except Exception:
        # If get_locked_folders fails (e.g., older schema), proceed anyway.
        # The atomic claim will still protect us at scan time.
        pass

    todo = [f for f in all_folders if str(f) not in skip_paths]
    
    if limit:
        todo = todo[:limit]
    
    if not todo:
        return {
            "folders_total": total,
            "folders_done": 0,
            "clean_count": 0,
            "corrupt_count": 0,
            "error_count": 0,
            "empty_count": 0,
        }
    
    # Scan statistics
    stats = {"clean_count": 0, "corrupt_count": 0, "error_count": 0, "empty_count": 0}
    done = 0
    
    def _scan_one(folder: Path):
        """Scan a single folder."""
        # Check for cancellation before starting
        if cancel_flag and cancel_flag():
            return None
        
        folder_str = str(folder)
        
        # Check if folder still exists
        if not folder.exists():
            return {
                "folder_path": folder_str,
                "video_path": None,
                "size_bytes": 0,
                "scan_state": "MISSING",
                "stderr_tail": "Folder no longer exists on disk",
                "last_scan_secs": 0.0,
            }
        
        # Atomically claim this folder for ourselves. If another PC has an
        # active lock, claim_for_scan returns False and we skip this folder
        # (some other worker is already on it).
        try:
            from config import WORKER_ID
            if not db.claim_for_scan(db_conn, folder_str, WORKER_ID):
                return {
                    "folder_path": folder_str,
                    "_skipped_locked": True,  # sentinel for the main loop
                }
        except Exception:
            # If claim fails for any reason, fall through and try to scan anyway.
            # Worst case: we duplicate work, not data corruption.
            pass
        
        # Notify that scan is starting for this folder
        if scan_start_callback:
            scan_start_callback(folder_str)
        
        video = largest_video_in_folder(folder)
        
        if not video:
            return {
                "folder_path": str(folder),
                "video_path": None,
                "size_bytes": 0,
                "scan_state": "EMPTY",
                "stderr_tail": "no video file in folder",
                "last_scan_secs": 0.0,
            }
        
        try:
            size = video.stat().st_size
        except OSError as e:
            return {
                "folder_path": str(folder),
                "video_path": str(video),
                "size_bytes": 0,
                "scan_state": "ERROR",
                "stderr_tail": f"stat failed: {e}",
                "last_scan_secs": 0.0,
            }
        
        # Progress callback for this specific file
        def file_progress(elapsed_sec):
            if file_progress_callback:
                file_progress_callback(str(folder), elapsed_sec)
        
        scan_state, stderr_tail, elapsed = null_decode(video, timeout_sec, progress_callback=file_progress)
        
        return {
            "folder_path": str(folder),
            "video_path": str(video),
            "size_bytes": size,
            "scan_state": scan_state,
            "stderr_tail": stderr_tail,
            "last_scan_secs": elapsed,
        }
    
    # Parallel scan with thread pool
    # Submit work in batches so we can check for cancellation
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {}
        folder_iter = iter(todo)
        
        # Submit initial batch (workers * 2 to keep pool full)
        for _ in range(min(workers * 2, len(todo))):
            try:
                folder = next(folder_iter)
                futures[pool.submit(_scan_one, folder)] = folder
            except StopIteration:
                break
        
        while futures:
            # Check for cancellation BEFORE waiting
            if cancel_flag and cancel_flag():
                # Kill all ffmpeg processes immediately
                _kill_ffmpeg_processes()
                # Cancel all pending futures
                for f in list(futures.keys()):
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                return {
                    "folders_total": total,
                    "folders_done": done,
                    **stats
                }
            
            # Wait for next completed future
            from concurrent.futures import wait, FIRST_COMPLETED
            completed, _ = wait(futures.keys(), timeout=1.0, return_when=FIRST_COMPLETED)
            
            if not completed:
                # Timeout - check cancel flag again
                continue
            
            for fut in completed:
                # Check cancellation after each completion
                if cancel_flag and cancel_flag():
                    for f in list(futures.keys()):
                        f.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    return {
                        "folders_total": total,
                        "folders_done": done,
                        **stats
                    }
                
                folder = futures.pop(fut)
                
                try:
                    result = fut.result()
                except Exception as exc:
                    result = {
                        "folder_path": str(folder),
                        "video_path": None,
                        "size_bytes": 0,
                        "scan_state": "ERROR",
                        "stderr_tail": f"task crashed: {exc}",
                        "last_scan_secs": 0.0,
                    }
                
                # Skip None results (cancelled)
                if result is None:
                    continue
                
                # Don't update DB if cancelled
                if cancel_flag and cancel_flag():
                    continue
                
                # Folder was claimed by another worker — skip silently.
                # Don't count toward stats; another PC is handling it.
                if result.get("_skipped_locked"):
                    # No DB update, no progress (other PC will report)
                    continue
                
                # Update database (this also overwrites our 'SCANNING' marker
                # with the final state) and clear our claim lock.
                try:
                    db.upsert_file_record(db_conn, result)
                    db.release_scan_claim(db_conn, result["folder_path"])
                except Exception as exc:
                    # If DB write fails, log but keep going
                    print(f"[scanner] DB update failed for {result['folder_path']}: {exc}", flush=True)
                
                # Update stats
                state = result["scan_state"]
                if state == "CLEAN":
                    stats["clean_count"] += 1
                elif state == "CORRUPT":
                    stats["corrupt_count"] += 1
                elif state == "ERROR":
                    stats["error_count"] += 1
                elif state == "EMPTY":
                    stats["empty_count"] += 1
                
                done += 1
                
                # Progress callback with completed result
                if progress_callback:
                    progress_callback(done, len(todo), result["folder_path"], state)
                
                # Submit next folder if available and not cancelled
                if not (cancel_flag and cancel_flag()):
                    try:
                        next_folder = next(folder_iter)
                        futures[pool.submit(_scan_one, next_folder)] = next_folder
                    except StopIteration:
                        pass
    finally:
        # Always shutdown the pool
        pool.shutdown(wait=False, cancel_futures=True)
    
    return {
        "folders_total": total,
        "folders_done": done,
        **stats
    }
