"""Video file scanner - null-decode corruption detection."""
import atexit
import collections
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
    """Kill all tracked ffmpeg processes immediately.

    Only affects ffmpeg WE launched (registered via _register_process), so it
    never disturbs unrelated ffmpeg (e.g. a parallel compressor encode).
    """
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


# Safety net: on a clean interpreter exit, kill any ffmpeg WE still have tracked
# so they never orphan. Only affects our own PIDs (not the compressor's encodes).
atexit.register(_kill_all_active_processes)


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


# Corruption triage: map ffmpeg error signatures to a human-readable diagnosis
# and a hint about whether a fresh re-download is likely to help. Ordered most
# specific first — the first matching signature wins.
#
# Each entry: (keyword, short_label, likely_fixable_by_redownload, explanation)
TRIAGE_RULES = (
    ("file ended prematurely",
     "Incomplete / truncated",
     True,
     "The file is cut short — bytes are missing from the end. Almost always an "
     "incomplete download or interrupted copy. A fresh re-download usually fixes it."),
    ("ended prematurely",
     "Incomplete / truncated",
     True,
     "The stream ends before it should. Typically an incomplete download or "
     "interrupted copy. A fresh re-download usually fixes it."),
    ("truncated",
     "Incomplete / truncated",
     True,
     "The file was cut off before the full stream was written. Re-downloading "
     "usually fixes it."),
    ("could not find codec parameters",
     "Missing / broken headers",
     False,
     "ffmpeg cannot read the stream headers. The container is malformed — often "
     "a bad encode at the source, not just a transfer error. A re-download may "
     "help only if a different (good) release exists."),
    ("invalid as first byte of an ebml",
     "Broken container (MKV)",
     False,
     "The Matroska/EBML container header is corrupt at the very start. The file "
     "structure itself is damaged — likely bad at the source or a disk write error."),
    ("invalid nal unit size",
     "Encoder artifact (H.264/H.265)",
     False,
     "The video bitstream has malformed NAL units. This is usually a bad encode "
     "at the source rather than a transfer problem."),
    ("decode_slice",
     "Encoder artifact (slice decode)",
     False,
     "A coded slice failed to decode. Usually indicates bitstream damage baked "
     "into the file at encode time."),
    ("missing reference",
     "Missing reference frames",
     True,
     "Frames reference other frames that are missing (broken GOP). Often the "
     "result of an incomplete download — a fresh copy usually fixes it."),
    ("non monotonically",
     "Timestamp (DTS/PTS) problem",
     False,
     "Decode timestamps are out of order. The file may still play in tolerant "
     "players, but the muxing is broken. Re-encoding rarely helps; a clean "
     "release is the reliable fix."),
    ("non-monotonous",
     "Timestamp (DTS/PTS) problem",
     False,
     "Decode timestamps are out of order. The file may still play in tolerant "
     "players, but the muxing is broken. A clean release is the reliable fix."),
    ("concealing",
     "Partial corruption (concealed)",
     True,
     "Some frames are damaged and ffmpeg is concealing the errors. Part of the "
     "file is fine; a fresh download typically restores the damaged region."),
    ("packet too large",
     "Malformed packet",
     False,
     "A packet declares an impossible size — structural corruption in the "
     "container. Usually damaged at the source or by a bad disk write."),
    ("invalid nal",
     "Encoder artifact (H.264/H.265)",
     False,
     "The video bitstream is malformed. Usually a bad encode at the source."),
    ("no frame",
     "No decodable frames",
     True,
     "ffmpeg found no decodable frames. The payload is missing or unreadable — "
     "a fresh download is usually required."),
    ("corrupt",
     "Generic corruption",
     True,
     "ffmpeg reported generic corruption. A fresh re-download is the usual fix; "
     "if it recurs, the source release itself may be bad."),
)


def triage_corruption(stderr: str):
    """Classify a CORRUPT stderr blob into a diagnosis.

    Returns a dict:
      {
        "label": short human label (e.g. "Incomplete / truncated"),
        "fixable": bool | None   # True likely fixed by re-download, False unlikely, None unknown
        "explanation": longer sentence,
      }
    Returns None when no known signature matches.
    """
    if not stderr:
        return None
    low = stderr.lower()
    for kw, label, fixable, explanation in TRIAGE_RULES:
        if kw in low:
            return {"label": label, "fixable": fixable, "explanation": explanation}
    return None


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


def _is_benign_muxer_line(low_line: str) -> bool:
    """True for output-side ffmpeg noise that is NOT file corruption.

    The `-f null` output muxer emits "non monotonically increasing dts to muxer"
    warnings (from `[null @ ...]` / `[out#...]`) for many perfectly playable
    files — especially B-frame rips with re-ordered timestamps. These must not
    be treated as corruption. A genuine *input/decode-side* DTS problem (e.g.
    from `[matroska,webm @ ...]`) does not carry the "to muxer" phrasing and is
    still caught.
    """
    return (
        "dts to muxer" in low_line
        or "provided invalid, non monotonic" in low_line
        or "provided invalid, non-monotonic" in low_line
        or low_line.lstrip().startswith("[null @")
        or low_line.lstrip().startswith("[out#")
    )


def _classify_stderr(stderr: str) -> str:
    """Check if stderr contains trouble keywords, ignoring benign muxer noise.

    Classifies line-by-line so a benign `-f null` muxer DTS warning on one line
    can't mask (or, worse, fabricate) a corruption verdict.
    """
    for raw in (stderr or "").splitlines():
        low = raw.lower()
        if not low.strip():
            continue
        if _is_benign_muxer_line(low):
            continue
        for kw in TROUBLE_KEYWORDS:
            if kw in low:
                return "CORRUPT"
    return "CLEAN"


def _probe_duration(video_path: Path) -> Optional[float]:
    """Return media duration in seconds via a fast ffprobe, or None.

    Used to convert ffmpeg's live decode position into a completion percentage.
    Cheap (sub-second) and best-effort — any failure returns None so the caller
    falls back to an indeterminate/pulsing bar.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        val = (proc.stdout or "").strip()
        d = float(val)
        return d if d > 0 else None
    except Exception:
        return None


def null_decode(video_path: Path, timeout_sec: int = 1800, progress_callback=None,
                cancel_flag=None, duration_sec: float = None) -> Tuple[str, str, float]:
    """
    Run ffmpeg null-decode to detect corruption.
    Returns: (scan_state, stderr_tail, elapsed_sec)
    scan_state is one of: CLEAN, CORRUPT, ERROR, TIMEOUT, CANCELLED

    progress_callback: optional function(elapsed_sec, fraction) called periodically.
        `fraction` is 0..1 decode progress when `duration_sec` is known, else None.
    cancel_flag: optional function() -> bool. When it returns True the scan is being
        cancelled; a non-zero ffmpeg exit in that case is because WE killed ffmpeg,
        not because the file is corrupt, so we return CANCELLED (which the caller
        must NOT record as a verdict).
    duration_sec: total media duration (from ffprobe) used to turn ffmpeg's live
        decode position into a real completion percentage.
    """
    start = time.time()
    proc = None
    
    # Adaptive timeout. Skip entirely when timeout_sec == 0 (No limit).
    # The budget must account for BOTH file size AND runtime: a long, low-bitrate
    # movie (e.g. a 2-hour HEVC encode that's only ~1.7 GB) needs far more wall
    # time than its size implies, especially over a slow NAS. Using size alone
    # caused long films to false-TIMEOUT. Budget = the most generous of:
    #   - the caller's base timeout
    #   - 2 min per GB (size-based, for big remuxes)
    #   - 1.5x the media runtime + 5 min slack (duration-based, for long films)
    if timeout_sec > 0:
        try:
            file_size_gb = video_path.stat().st_size / (1024**3)
            budget = max(timeout_sec, int(file_size_gb * 120))
            if duration_sec and duration_sec > 0:
                budget = max(budget, int(duration_sec * 1.5) + 300)
            timeout_sec = budget
        except Exception:
            pass  # Use default if we can't stat
    
    # Stall detection: if the decode position stops advancing for this long, the
    # decode is genuinely hung (dead NAS read / pathological stream) rather than
    # slow. Timing out on a stall catches "stuck at 0%" cases fast instead of
    # burning the whole (now larger) budget. Configurable via config.STALL_LIMIT_SEC
    # (env REPAIR_STALL_LIMIT_SEC); 0 disables stall detection.
    try:
        import config as _config
        STALL_LIMIT_SEC = int(getattr(_config, "STALL_LIMIT_SEC", 300))
    except Exception:
        STALL_LIMIT_SEC = 300
    last_progress_pos = -1.0
    last_progress_change = time.time()
    
    # Shared decode-position clock (seconds), updated by a stdout reader thread
    # consuming ffmpeg's machine-readable "-progress pipe:1" output.
    last_pos = [0.0]
    prog_thread = None
    
    def _consume_progress(stream):
        try:
            for pline in stream:
                pline = pline.strip()
                if pline.startswith("out_time_us=") or pline.startswith("out_time_ms="):
                    try:
                        val = int(pline.split("=", 1)[1])
                        if val >= 0:
                            last_pos[0] = val / 1_000_000.0
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass

    # Concurrent stderr drainer. CRITICAL: some files (e.g. HEVC rips with broken
    # container DTS) make ffmpeg emit tens of thousands of benign
    # "non monotonic DTS to muxer" lines. If stderr isn't drained continuously,
    # the OS pipe buffer (~64 KB) fills, ffmpeg BLOCKS on write(), the decode
    # position freezes, and the stall detector kills a perfectly healthy decode.
    # Draining in a thread prevents that deadlock. We keep only a bounded tail so
    # a multi-GB flood can't exhaust memory — the verdict only needs the tail.
    stderr_tail_lines = collections.deque(maxlen=2000)

    def _consume_stderr(stream):
        try:
            for eline in stream:
                stderr_tail_lines.append(eline)
        except Exception:
            pass

    err_thread = None
    
    try:
        # -progress pipe:1 -> machine-readable decode position on stdout.
        # stderr stays reserved for -v error corruption lines.
        # -fflags +igndts: don't enforce monotonic DTS at the (null) muxer. We
        # only decode to surface real decode/demux errors; the muxer's DTS
        # bookkeeping is irrelevant and on some files floods stderr with tens of
        # thousands of benign warnings (which used to stall the whole decode).
        proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-fflags", "+igndts",
             "-progress", "pipe:1",
             "-i", str(video_path), "-f", "null", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        
        # Register for tracking so we can kill it from outside
        _register_process(proc)
        
        # Reader thread for the progress pipe (stdout).
        prog_thread = threading.Thread(target=_consume_progress, args=(proc.stdout,), daemon=True)
        prog_thread.start()
        # Reader thread for stderr (prevents the pipe-full deadlock above).
        err_thread = threading.Thread(target=_consume_stderr, args=(proc.stderr,), daemon=True)
        err_thread.start()
        
        # Poll process and emit progress updates
        while proc.poll() is None:
            # If the scan is being cancelled, stop ffmpeg and report CANCELLED
            # (a killed ffmpeg exits non-zero, which must NOT be read as CORRUPT).
            if cancel_flag and cancel_flag():
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                return "CANCELLED", "scan cancelled", time.time() - start
            
            # Check if we've exceeded the overall timeout budget
            elapsed = time.time() - start
            if timeout_sec > 0 and elapsed > timeout_sec:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except:
                    pass
                # Use TIMEOUT state (not ERROR) so users can rescan with longer timeout
                return "TIMEOUT", f"TIMEOUT after {timeout_sec}s (decode exceeded time budget)", elapsed
            
            # Stall detection: has the decode position advanced recently?
            now = time.time()
            if last_pos[0] > last_progress_pos:
                last_progress_pos = last_pos[0]
                last_progress_change = now
            # Only treat as a stall once we've given it a grace period AND the
            # position never moved (or stopped moving) for STALL_LIMIT_SEC.
            # STALL_LIMIT_SEC == 0 disables stall detection entirely.
            if (STALL_LIMIT_SEC > 0 and timeout_sec != 0
                    and (now - last_progress_change) > STALL_LIMIT_SEC and elapsed > 30):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                pos_txt = f"{last_progress_pos:.0f}s" if last_progress_pos > 0 else "the start"
                return ("TIMEOUT",
                        f"STALLED: decode made no progress for {STALL_LIMIT_SEC}s "
                        f"(stuck near {pos_txt}) - likely a hung read or bad stream",
                        elapsed)
            
            # Emit progress callback with a real fraction when duration is known.
            if progress_callback:
                frac = None
                if duration_sec and duration_sec > 0:
                    frac = max(0.0, min(1.0, last_pos[0] / duration_sec))
                progress_callback(elapsed, frac)
            
            time.sleep(0.5)
        
        # Process completed. stderr/stdout are being drained by their reader
        # threads; wait for the process to fully reap, then join the drainers so
        # we've collected the final output before building the verdict.
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        if prog_thread:
            prog_thread.join(timeout=2)
        if err_thread:
            err_thread.join(timeout=2)
        # Final 100% tick
        if progress_callback:
            progress_callback(time.time() - start, 1.0 if (duration_sec and duration_sec > 0) else None)
        
        stderr_output = ''.join(stderr_tail_lines)
        
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
    
    # If cancellation kicked in right as ffmpeg ended, a non-zero exit means WE
    # killed it — not corruption. Don't record a verdict.
    if cancel_flag and cancel_flag():
        return "CANCELLED", "scan cancelled", elapsed
    
    if proc.returncode != 0:
        # ffmpeg exited non-zero -- almost always corruption it couldn't push past
        reason = stderr_tail or f"ffmpeg exit {proc.returncode}"
        return "CORRUPT", _tag_triage(stderr_output, reason), elapsed
    
    # Exit 0 but stderr might contain trouble keywords (the 28YL pattern)
    verdict = _classify_stderr(stderr_output or "")
    if verdict == "CORRUPT":
        return verdict, _tag_triage(stderr_output, stderr_tail), elapsed
    return verdict, stderr_tail, elapsed


def _tag_triage(stderr_full: str, reason: str) -> str:
    """Prefix a corruption reason with a short triage label when recognized.

    Produces e.g. "[Incomplete / truncated] file ended prematurely ...".
    The GUI reads this from stderr_tail; the bracketed label makes the type
    obvious at a glance in the Reason column.
    """
    triage = triage_corruption(stderr_full or reason or "")
    if not triage:
        return reason
    return f"[{triage['label']}] {reason}".strip()


def _run_capture(cmd: list, timeout: int = 120):
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", f"{cmd[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return -2, "", f"{cmd[0]} timed out after {timeout}s"
    except Exception as exc:
        return -3, "", f"exec failure: {exc}"


def deep_inspect(video_path: Path, progress_callback: Optional[Callable] = None) -> dict:
    """Run a battery of ffprobe/ffmpeg diagnostics on a single file.

    This is an on-demand, read-only diagnostic used to decide whether a file
    flagged CORRUPT is truly unrecoverable or just has a fixable problem
    (e.g. an incomplete download). It runs three checks:

      1. ffprobe   — full container/stream metadata (codec, duration, streams)
      2. header    — decode the first moment only, to test container validity
      3. tail      — decode the last ~5% only, to detect truncation

    Interpretation logic:
      - header OK + full decode fails only near the end  -> truncated / incomplete
      - header fails                                      -> container-level damage
      - fails uniformly throughout                        -> encoder/source corruption

    Args:
      progress_callback: optional fn(fraction_0_to_1, label) called at each
        phase boundary so a UI can show a determinate progress bar. The three
        phases are cheap boundaries — this adds no measurable overhead.

    Returns a dict with keys:
      ok (bool)          - True if ffprobe itself succeeded
      probe (dict|None)  - parsed ffprobe JSON (format + streams) if available
      summary (str)      - human-readable multi-line report
      diagnosis (str)    - one-line conclusion
      report (str)       - full text suitable for a dialog
    """
    import json as _json

    def _progress(frac, label):
        if progress_callback:
            try:
                progress_callback(frac, label)
            except Exception:
                pass

    video_path = Path(video_path)
    lines: list = []
    diagnosis = "Inconclusive"

    if not video_path.exists():
        return {
            "ok": False,
            "probe": None,
            "summary": "File no longer exists on disk.",
            "diagnosis": "File missing",
            "report": f"{video_path}\n\nFile no longer exists on disk.",
        }

    _progress(0.05, "Reading metadata (ffprobe)...")

    lines.append(f"File: {video_path.name}")
    try:
        size_gb = video_path.stat().st_size / (1024 ** 3)
        lines.append(f"Size: {size_gb:.2f} GB")
    except OSError:
        pass
    lines.append("")

    # ---- 1. ffprobe: container + stream metadata ---------------------------
    rc, out, err = _run_capture(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(video_path),
        ],
        timeout=120,
    )

    probe = None
    duration_sec = None
    if rc == 0 and out.strip():
        try:
            probe = _json.loads(out)
        except ValueError:
            probe = None

    if probe:
        fmt = probe.get("format", {})
        streams = probe.get("streams", [])
        fmt_name = fmt.get("format_long_name") or fmt.get("format_name") or "?"
        try:
            duration_sec = float(fmt.get("duration")) if fmt.get("duration") else None
        except (TypeError, ValueError):
            duration_sec = None

        lines.append("=== ffprobe: container ===")
        lines.append(f"Container: {fmt_name}")
        if duration_sec:
            m, s = divmod(int(duration_sec), 60)
            h, m = divmod(m, 60)
            lines.append(f"Duration : {h:d}h {m:02d}m {s:02d}s")
        else:
            lines.append("Duration : UNKNOWN (bad sign — headers may be damaged)")
        bitrate = fmt.get("bit_rate")
        if bitrate:
            try:
                lines.append(f"Bitrate  : {int(bitrate) / 1_000_000:.1f} Mbps")
            except (TypeError, ValueError):
                pass

        lines.append("")
        lines.append(f"=== ffprobe: {len(streams)} stream(s) ===")
        video_streams = 0
        for st in streams:
            codec_type = st.get("codec_type", "?")
            codec = st.get("codec_name", "?")
            if codec_type == "video":
                video_streams += 1
                prof = st.get("profile", "")
                w = st.get("width", "?")
                h = st.get("height", "?")
                pix = st.get("pix_fmt", "?")
                lines.append(
                    f"  video: {codec} {prof} {w}x{h} {pix}".rstrip()
                )
            elif codec_type == "audio":
                ch = st.get("channels", "?")
                lang = st.get("tags", {}).get("language", "")
                lines.append(f"  audio: {codec} {ch}ch {lang}".rstrip())
            else:
                lines.append(f"  {codec_type}: {codec}")
        if video_streams == 0:
            lines.append("  WARNING: no decodable video stream found.")
    else:
        lines.append("=== ffprobe FAILED ===")
        lines.append((err or "ffprobe produced no output").strip()[:500])
        if rc == -1:
            # ffprobe missing entirely — bail early, nothing else will run
            return {
                "ok": False,
                "probe": None,
                "summary": "\n".join(lines),
                "diagnosis": "ffprobe not installed",
                "report": "\n".join(lines),
            }

    lines.append("")

    # ---- 2. header-only decode: is the container itself readable? ----------
    # `-t 0` reads/initializes streams without decoding frames.
    _progress(0.40, "Checking container headers...")
    lines.append("=== Header integrity (container) ===")
    h_rc, _h_out, h_err = _run_capture(
        ["ffmpeg", "-v", "error", "-i", str(video_path), "-t", "0", "-f", "null", "-"],
        timeout=60,
    )
    header_ok = (h_rc == 0 and not h_err.strip())
    if header_ok:
        lines.append("  OK — container headers parse cleanly.")
    else:
        lines.append("  FAILED — headers/container are damaged:")
        lines.append("  " + (h_err.strip()[:300] or f"ffmpeg exit {h_rc}"))

    lines.append("")

    # ---- 3. tail decode: is only the END broken (i.e. truncated)? ----------
    tail_ok = None
    tail_err = ""
    if duration_sec and duration_sec > 30:
        # Seek to ~5% before the end and decode to the end.
        _progress(0.70, "Decoding tail (last ~5%)...")
        seek_to = max(0.0, duration_sec * 0.95)
        lines.append("=== Tail decode (last ~5%) ===")
        t_rc, _t_out, tail_err = _run_capture(
            [
                "ffmpeg", "-v", "error",
                "-ss", f"{seek_to:.2f}",
                "-i", str(video_path),
                "-f", "null", "-",
            ],
            timeout=180,
        )
        tail_ok = (t_rc == 0 and not tail_err.strip())
        if tail_ok:
            lines.append("  OK — the end of the file decodes cleanly.")
        else:
            lines.append("  FAILED — the end of the file is damaged:")
            lines.append("  " + (tail_err.strip()[:300] or f"ffmpeg exit {t_rc}"))
        lines.append("")

    # ---- Diagnosis synthesis ----------------------------------------------
    triage = triage_corruption(h_err or tail_err or "")

    # `ambiguous` means the quick checks couldn't localize the damage: the
    # header and the end both look fine, so whatever the full scan tripped on
    # lives in the un-probed middle. Only a full decode can characterize it.
    ambiguous = False
    # `fixable` summarizes whether re-downloading is expected to help:
    #   True  -> re-download the SAME release will very likely fix it
    #   False -> the source itself is bad; a re-download of the same release
    #            probably won't help (need a different release)
    #   None  -> unknown from the quick check (run a full decode)
    fixable = None

    if not header_ok:
        fixable = False
        diagnosis = (
            "Container-level damage — the file structure itself is broken. "
            "Likely a bad source release or a disk-write error, not just a "
            "partial download. Re-downloading a DIFFERENT release is the fix."
        )
    elif tail_ok is False and header_ok:
        fixable = True
        diagnosis = (
            "Header is fine but the END is damaged — classic TRUNCATED / "
            "INCOMPLETE download. Re-downloading the same release should fix it."
        )
    elif tail_ok is True and header_ok:
        ambiguous = True
        diagnosis = (
            "Headers and the file's end both decode cleanly, so any damage is "
            "in the un-probed MIDDLE of the file. This quick check can't tell "
            "how much or where. Run a full deep decode to map the exact error "
            "locations and get a definitive verdict."
        )
    elif header_ok and tail_ok is None:
        ambiguous = True
        diagnosis = (
            "Headers parse cleanly but the file is too short to sample a tail. "
            "If a full scan flagged it, the damage is mid-stream. Run a full "
            "deep decode to map where the errors are."
        )

    if triage:
        fixhint = (
            "likely fixable by re-download"
            if triage["fixable"]
            else "re-download may NOT help (source likely bad)"
        )
        lines.append("=== Corruption triage ===")
        lines.append(f"  Type : {triage['label']} ({fixhint})")
        lines.append(f"  Note : {triage['explanation']}")
        lines.append("")

    lines.append("=== Diagnosis ===")
    lines.append(diagnosis)

    if ambiguous:
        lines.append("")
        lines.append(
            "→ Recommendation: run a FULL DEEP DECODE to pinpoint the damage."
        )

    _progress(1.0, "Done")
    report = "\n".join(lines)
    return {
        "ok": probe is not None,
        "probe": probe,
        "ambiguous": ambiguous,
        "fixable": fixable,
        "duration_sec": duration_sec,
        "summary": report,
        "diagnosis": diagnosis,
        "report": report,
    }


# Substrings that mark an stderr line as BENIGN output-side noise rather than
# real corruption. The dominant one is the `-f null` muxer complaining about
# non-monotonic DTS, which many clean B-frame rips produce in huge volume.
_BENIGN_ERROR_MARKERS = (
    "non monotonically increasing dts to muxer",
    "non-monotonically increasing dts to muxer",
    "application provided invalid, non monotonically",
    "application provided invalid, non-monotonically",
    "dts to muxer",
    "[null @",          # the null output muxer itself
    "[out#",            # output-stream bookkeeping
    "last message repeated",
)

# Substrings that DO indicate genuine decode/demux corruption. If any of these
# appear the line is counted regardless of the benign filter above.
_REAL_ERROR_MARKERS = (
    "error while decoding",
    "concealing",
    "corrupt",
    "truncated",
    "invalid nal unit size",
    "missing picture",
    "no frame",
    "decode_slice",
    "missing reference",
    "could not find codec parameters",
    "invalid data found",
    "ended prematurely",
    "non-existing pps",
    "non-existing sps",
    "sei type",
    "mmco",
    "illegal",
    "out of range",
    "marker does not match",
    "exceeds containing master element",  # broken MKV element sizes
    "ac-tex damaged",
    "slice mismatch",
    "cbp too large",
    "error splitting the input",
)


def _is_real_decode_error(line: str) -> bool:
    """Decide whether an ffmpeg -v error stderr line is genuine corruption.

    The full-decode uses `-f null`, whose muxer prints benign non-monotonic DTS
    warnings for many perfectly playable files. Those must not be counted as
    corruption. A line counts as a real error only when it contains a known
    corruption marker AND is not purely benign output-muxer noise.
    """
    low = line.lower()
    # Real-corruption markers win outright.
    for kw in _REAL_ERROR_MARKERS:
        if kw in low:
            return True
    # Otherwise, drop known-benign output/muxer chatter.
    for kw in _BENIGN_ERROR_MARKERS:
        if kw in low:
            return False
    # Unknown line at -v error: be conservative and DO NOT count it. Genuine
    # corruption almost always matches a real marker above; anything else is
    # far more likely to be muxer/output bookkeeping than actual damage.
    return False


def full_decode_error_map(video_path: Path, duration_sec: Optional[float] = None,
                          progress_callback: Optional[Callable] = None,
                          cancel_flag: Optional[Callable] = None) -> dict:
    """Fully decode a file and map exactly where decode errors occur.

    This is the expensive, definitive check — it decodes every frame (same cost
    as the original null-decode scan) but keeps ffmpeg's per-error output and
    turns it into an actionable report:

      * total error count
      * where the errors fall on the timeline (bucketed into 20 segments)
      * whether damage is localized (one bad region → likely a bad download
        chunk) or pervasive (spread throughout → likely a bad source release)
      * a severity VERDICT with a recommended action

    Args:
      video_path: file to decode
      duration_sec: known duration (from ffprobe) used to place errors on the
        timeline and to compute a progress percentage. Optional.
      progress_callback: fn(fraction_0_to_1 or None, elapsed_sec) called ~2x/sec
      cancel_flag: fn() -> bool; return True to abort the decode early

    Returns a dict:
      completed (bool)   - False if cancelled/aborted before finishing
      error_count (int)
      verdict (str)      - one of PLAYABLE / RE-DOWNLOAD / BAD SOURCE / CLEAN
      recommendation (str)
      report (str)       - full multi-line text for a dialog
    """
    video_path = Path(video_path)
    start = time.time()

    if not video_path.exists():
        return {
            "completed": False,
            "error_count": 0,
            "verdict": "MISSING",
            "recommendation": "File no longer exists on disk.",
            "report": f"{video_path}\n\nFile no longer exists on disk.",
        }

    # We need two things at once, from two separate ffmpeg streams:
    #   * stdout: machine-readable "-progress pipe:1" emits `out_time_us=<n>`
    #     very frequently (per packet) — our reliable decode-position clock.
    #   * stderr: at "-v error", lines about decode/demux problems — BUT also
    #     harmless muxer chatter we must NOT count (see _is_real_decode_error).
    # A background thread consumes stdout progress and updates a shared
    # `last_time_sec[0]`; the main loop reads stderr and stamps each real error
    # with whatever the current decode position is.
    proc = None
    error_points: List[Tuple[float, str]] = []
    ignored_lines: List[str] = []  # benign noise, kept only for the report
    last_time_sec = [0.0]          # boxed so the reader thread can mutate it
    reader_thread = None

    def _consume_progress(stdout_stream):
        try:
            for pline in stdout_stream:
                pline = pline.strip()
                if pline.startswith("out_time_us="):
                    try:
                        us = int(pline.split("=", 1)[1])
                        if us >= 0:
                            last_time_sec[0] = us / 1_000_000.0
                    except (ValueError, IndexError):
                        pass
                elif pline.startswith("out_time_ms="):
                    # older ffmpeg builds emit out_time_ms (actually microseconds)
                    try:
                        val = int(pline.split("=", 1)[1])
                        if val >= 0:
                            last_time_sec[0] = val / 1_000_000.0
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass

    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error",
             "-progress", "pipe:1", "-i", str(video_path), "-f", "null", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _register_process(proc)

        reader_thread = threading.Thread(
            target=_consume_progress, args=(proc.stdout,), daemon=True
        )
        reader_thread.start()

        last_cb = 0.0
        for raw_line in proc.stderr:
            if cancel_flag and cancel_flag():
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                return {
                    "completed": False,
                    "error_count": len(error_points),
                    "verdict": "ABORTED",
                    "recommendation": "Full decode was cancelled before completion.",
                    "report": "Full decode cancelled.",
                }

            line = raw_line.rstrip("\n")
            if not line:
                continue

            # Not every -v error line is corruption. The `-f null` output muxer
            # emits benign "non monotonically increasing dts to muxer" style
            # warnings for many perfectly playable files (B-frame / edited-DTS
            # rips). Only count lines that indicate real decode/demux damage.
            if not _is_real_decode_error(line):
                if len(ignored_lines) < 20:
                    ignored_lines.append(line)
                continue

            error_points.append((last_time_sec[0], line))

            # Emit a throttled progress update off the current decode position.
            now = time.time()
            if progress_callback and (now - last_cb) >= 0.5:
                frac = (last_time_sec[0] / duration_sec) if duration_sec else None
                if frac is not None:
                    frac = max(0.0, min(1.0, frac))
                progress_callback(frac, now - start)
                last_cb = now

        proc.wait()
        # Drain the progress thread and fire a final 100% progress tick.
        if reader_thread:
            reader_thread.join(timeout=2)
        if progress_callback:
            progress_callback(1.0 if duration_sec else None, time.time() - start)
    except FileNotFoundError:
        return {
            "completed": False,
            "error_count": 0,
            "verdict": "ERROR",
            "recommendation": "ffmpeg not found on PATH.",
            "report": "ffmpeg not found on PATH.",
        }
    except Exception as exc:
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        return {
            "completed": False,
            "error_count": len(error_points),
            "verdict": "ERROR",
            "recommendation": f"Decode failed: {exc}",
            "report": f"Decode failed: {exc}",
        }
    finally:
        if proc:
            _unregister_process(proc)

    elapsed = time.time() - start
    error_count = len(error_points)

    # ---- Build the timeline histogram (20 buckets) -------------------------
    N_BUCKETS = 20
    buckets = [0] * N_BUCKETS
    total_dur = duration_sec or (max((t for t, _ in error_points), default=0.0) or 1.0)
    for t, _msg in error_points:
        if total_dur > 0:
            idx = int((t / total_dur) * N_BUCKETS)
            idx = max(0, min(N_BUCKETS - 1, idx))
        else:
            idx = 0
        buckets[idx] += 1
    affected_buckets = sum(1 for b in buckets if b > 0)
    spread_pct = (affected_buckets / N_BUCKETS) * 100.0

    # ---- Severity verdict --------------------------------------------------
    # Heuristics tuned for "is this watchable / is the source bad?":
    #   - 0 errors             → CLEAN (the earlier scan may have been transient)
    #   - few errors, localized → PLAYABLE (a brief glitch)
    #   - many errors OR spread across most of the file → decide RE-DOWNLOAD vs BAD SOURCE
    if error_count == 0:
        verdict = "CLEAN"
        if ignored_lines:
            recommendation = (
                "A full decode found NO real corruption. ffmpeg did emit "
                f"{len(ignored_lines)}+ benign muxer timestamp warnings (non-monotonic "
                "DTS from the '-f null' output), but those do NOT indicate a broken "
                "file — many perfectly playable B-frame rips produce them. This file "
                "is almost certainly fine; re-scan to clear the CORRUPT flag."
            )
        else:
            recommendation = (
                "A full decode found NO errors. The earlier CORRUPT flag was likely "
                "transient (a slow NAS read or a since-fixed file). Re-scan to clear it."
            )
    elif error_count <= 5 and affected_buckets <= 2:
        verdict = "PLAYABLE"
        recommendation = (
            f"Only {error_count} error(s) in a small region — expect a brief "
            "glitch of a second or two. The file is almost certainly watchable. "
            "Re-download is optional."
        )
    elif spread_pct >= 60.0:
        verdict = "BAD SOURCE"
        recommendation = (
            f"Errors are spread across ~{spread_pct:.0f}% of the runtime "
            f"({error_count} total). This is pervasive corruption, typical of a "
            "bad encode/release rather than a transfer glitch. Re-downloading the "
            "SAME release will probably fail again — seek a different release."
        )
    else:
        verdict = "RE-DOWNLOAD"
        recommendation = (
            f"{error_count} errors concentrated in ~{affected_buckets}/{N_BUCKETS} "
            "of the timeline. Localized damage like this is usually a corrupted "
            "download chunk — re-downloading the same release will likely fix it."
        )

    # ---- Render report -----------------------------------------------------
    lines: List[str] = []
    lines.append(f"File: {video_path.name}")
    if duration_sec:
        m_, s_ = divmod(int(duration_sec), 60)
        h_, m_ = divmod(m_, 60)
        lines.append(f"Duration: {h_:d}h {m_:02d}m {s_:02d}s")
    lines.append(f"Decode time: {elapsed:.0f}s")
    lines.append("")
    lines.append(f"Total decode errors: {error_count}")
    lines.append(f"Timeline coverage : {affected_buckets}/{N_BUCKETS} segments affected "
                 f"({spread_pct:.0f}% of runtime)")
    if ignored_lines:
        lines.append(f"Ignored (benign)  : {len(ignored_lines)}+ muxer/output warnings "
                     f"(non-monotonic DTS etc.) — not corruption")
    lines.append("")

    if error_count > 0:
        lines.append("=== Error map (each segment ~5% of runtime) ===")
        seg_dur = total_dur / N_BUCKETS if total_dur else 0
        for i, count in enumerate(buckets):
            seg_start = seg_dur * i
            mm, ss = divmod(int(seg_start), 60)
            hh, mm = divmod(mm, 60)
            label = f"{hh:d}:{mm:02d}:{ss:02d}"
            bar = "#" * min(count, 40) if count else "."
            suffix = f" {count}" if count else ""
            lines.append(f"  {label}  {bar}{suffix}")
        lines.append("")

        # Show a few representative raw error messages.
        lines.append("=== Sample errors ===")
        seen = set()
        shown = 0
        for _t, msg in error_points:
            key = msg[:80]
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  {msg[:160]}")
            shown += 1
            if shown >= 8:
                break
        lines.append("")

    # When there was no real corruption but we saw benign muxer noise, show a
    # couple of examples so it's clear what was (correctly) ignored.
    if error_count == 0 and ignored_lines:
        lines.append("=== Ignored (benign muxer/output warnings) ===")
        for msg in ignored_lines[:5]:
            lines.append(f"  {msg[:160]}")
        lines.append("  (These are output-side timestamp warnings, not file damage.)")
        lines.append("")

    lines.append("=== VERDICT ===")
    lines.append(verdict)
    lines.append(recommendation)

    # Whether re-downloading the SAME release is expected to help. RE-DOWNLOAD
    # (localized damage) is the clear yes; the others are no / not-applicable.
    fixable = verdict == "RE-DOWNLOAD"

    report = "\n".join(lines)
    return {
        "completed": True,
        "error_count": error_count,
        "verdict": verdict,
        "recommendation": recommendation,
        "spread_pct": spread_pct,
        "buckets": buckets,
        "fixable": fixable,
        "report": report,
    }


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
                 size_known_callback: Optional[Callable] = None,
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
    - size_known_callback: optional function(folder_path, size_bytes) - called when file size is known, before ffmpeg starts
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
    #   1. A definitive prior verdict (CLEAN/CORRUPT/EMPTY) AND the file is
    #      UNCHANGED since (same size + mtime). A CLEAN file that hasn't changed
    #      on disk never needs re-scanning — the result is deterministic. This
    #      replaces the old crude "skip if scanned < 7 days" rule.
    #   2. Currently locked by another worker (multi-PC mode)
    #
    # Records with no stored mtime (scanned before this feature existed) can't
    # be proven unchanged, so they are re-scanned once to capture a baseline.
    skip_paths = set()

    if not rescan:
        existing = db.get_files(db_conn)
        definitive = ("CLEAN", "CORRUPT", "EMPTY")
        for r in existing:
            if r.get("scan_state") not in definitive:
                continue
            stored_mtime = r.get("mtime")
            stored_size = r.get("size_bytes")
            if stored_mtime is None or not stored_size:
                # No baseline to compare — must re-scan once to record it.
                continue
            video_path = r.get("video_path")
            if not video_path:
                continue
            try:
                st = os.stat(video_path)
            except OSError:
                # File/folder gone or unreadable — let the scan handle it
                # (it will mark MISSING/ERROR appropriately).
                continue
            # Unchanged if both size and mtime match (allow tiny fs mtime jitter).
            if st.st_size == stored_size and abs(st.st_mtime - float(stored_mtime)) < 2.0:
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
            "timeout_count": 0,
            "missing_count": 0,
        }
    
    # Scan statistics
    stats = {
        "clean_count": 0,
        "corrupt_count": 0,
        "error_count": 0,
        "empty_count": 0,
        "timeout_count": 0,
        "missing_count": 0,
    }
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
            st = video.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError as e:
            return {
                "folder_path": str(folder),
                "video_path": str(video),
                "size_bytes": 0,
                "scan_state": "ERROR",
                "stderr_tail": f"stat failed: {e}",
                "last_scan_secs": 0.0,
            }
        
        # Notify that file size is known (before ffmpeg starts)
        if size_known_callback:
            size_known_callback(folder_str, size)
        
        # Fast ffprobe for duration so the per-file bar can show a real % (a
        # sub-second call; failure just falls back to a pulsing bar).
        duration = _probe_duration(video)
        
        # Progress callback for this specific file (forwards % when known).
        def file_progress(elapsed_sec, frac=None):
            if file_progress_callback:
                file_progress_callback(str(folder), elapsed_sec, frac)
        
        scan_state, stderr_tail, elapsed = null_decode(
            video, timeout_sec, progress_callback=file_progress,
            cancel_flag=cancel_flag, duration_sec=duration
        )
        
        # A cancelled decode never produced a real verdict — signal the main
        # loop to leave this folder alone (it will be reset to UNKNOWN).
        if scan_state == "CANCELLED":
            return {"folder_path": str(folder), "_cancelled": True}
        
        return {
            "folder_path": str(folder),
            "video_path": str(video),
            "size_bytes": size,
            "mtime": mtime,
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
                
                # Skip None results (cancelled before scan started)
                if result is None:
                    continue
                
                # Folder was claimed by another worker — skip silently.
                # Don't count toward stats; another PC is handling it.
                if result.get("_skipped_locked"):
                    # No DB update, no progress (other PC will report)
                    continue
                
                # Decode was cancelled mid-flight — do NOT record a verdict
                # (a killed ffmpeg looks non-zero but isn't corruption). Reset
                # the folder to UNKNOWN so the next scan re-checks it.
                if result.get("_cancelled"):
                    try:
                        fp = result["folder_path"]
                        table = db._files_table(db_conn)
                        ph = db._ph(db_conn)
                        db._execute(
                            db_conn,
                            f"UPDATE {table} SET scan_state='UNKNOWN', worker_id=NULL, "
                            f"lock_until=NULL WHERE folder_path={ph}",
                            (fp,),
                        )
                    except Exception:
                        pass
                    continue
                
                # Always write completed results to DB, even if cancelled —
                # a result that finished deserves to be recorded.
                try:
                    db.upsert_file_record(db_conn, result)
                    db.release_scan_claim(db_conn, result["folder_path"])
                    # Append to the persistent scan-activity log (append-only feed).
                    db.record_scan_event(
                        db_conn,
                        result["folder_path"],
                        result["scan_state"],
                        result.get("last_scan_secs"),
                        result.get("stderr_tail"),
                    )
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
                elif state == "TIMEOUT":
                    stats["timeout_count"] += 1
                elif state == "MISSING":
                    stats["missing_count"] += 1
                
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
