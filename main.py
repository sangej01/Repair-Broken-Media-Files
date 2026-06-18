"""Entry point for Repair Broken Media Files tool."""
import argparse
import sys
import time
from pathlib import Path

import db
import scanner
import config


def launch_gui():
    """Launch the PySide6 GUI."""
    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow
    
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)  # Ensure app quits when window closes
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def cli_scan(args):
    """CLI: repair scan"""
    parser = argparse.ArgumentParser(description="Scan library for corrupt files")
    parser.add_argument("--workers", type=int, default=2, help="Concurrent ffmpeg workers (default: 2)")
    parser.add_argument("--root", action="append", help="Library root to scan (repeatable)")
    parser.add_argument("--rescan", action="store_true", help="Re-scan all folders, ignoring recent scans")
    parser.add_argument("--limit", type=int, help="Limit scan to N folders (for testing)")
    parser.add_argument("--timeout", type=int, default=1800, help="Per-file ffmpeg timeout in seconds (default: 1800)")
    opts = parser.parse_args(args)
    
    # Get library roots
    if opts.root:
        roots = [Path(r) for r in opts.root]
    else:
        roots = config.get_library_roots()
    
    print(f"Scanning {len(roots)} library root(s) with {opts.workers} worker(s)")
    for r in roots:
        print(f"  {r}")
    
    # Initialize database
    conn = db.init_db()
    
    # Record run start
    run_id = db.record_run_start(conn, "scan", {
        "workers": opts.workers,
        "roots": [str(r) for r in roots],
        "rescan": opts.rescan,
        "limit": opts.limit,
    })
    
    # Progress callback
    start_time = time.time()
    def progress(current, total, folder_path, state):
        elapsed = time.time() - start_time
        rate = current / max(elapsed, 1)
        eta_sec = (total - current) / rate if rate > 0 else 0
        eta_h = eta_sec / 3600
        flag = "  ★" if state == "CORRUPT" else ""
        folder_name = Path(folder_path).name if folder_path else ""
        print(f"[{current:>5}/{total}] {state:7s}  ETA {eta_h:5.1f}h  {folder_name}{flag}", flush=True)
    
    # Run scan
    print("\nScanning...")
    stats = scanner.scan_library(
        roots=roots,
        workers=opts.workers,
        db_conn=conn,
        progress_callback=progress,
        rescan=opts.rescan,
        limit=opts.limit,
        timeout_sec=opts.timeout
    )
    
    # Record run finish
    db.record_run_finish(conn, run_id, stats)
    
    # Print summary
    elapsed = time.time() - start_time
    print(f"\n=== Scan complete in {elapsed/3600:.2f}h ===")
    print(f"  Folders total:  {stats['folders_total']}")
    print(f"  Folders done:   {stats['folders_done']}")
    print(f"  CLEAN:          {stats['clean_count']}")
    print(f"  CORRUPT:        {stats['corrupt_count']}")
    print(f"  ERROR:          {stats['error_count']}")
    print(f"  EMPTY:          {stats['empty_count']}")
    print(f"\nDatabase: {config.DB_PATH}")
    
    conn.close()


def cli_list(args):
    """CLI: repair list"""
    parser = argparse.ArgumentParser(description="List scanned files")
    parser.add_argument("--corrupt", action="store_true", help="Show only CORRUPT files")
    parser.add_argument("--clean", action="store_true", help="Show only CLEAN files")
    parser.add_argument("--error", action="store_true", help="Show only ERROR files")
    parser.add_argument("--empty", action="store_true", help="Show only EMPTY files")
    parser.add_argument("--queued", action="store_true", help="Show files queued for remediation")
    parser.add_argument("--sort", default="size_bytes", choices=["size_bytes", "folder_path", "last_scan_at"], 
                       help="Sort by field (default: size_bytes)")
    opts = parser.parse_args(args)
    
    # Initialize database
    conn = db.init_db()
    
    # Determine filter
    filter_state = None
    filter_remediation = None
    if opts.corrupt:
        filter_state = "CORRUPT"
    elif opts.clean:
        filter_state = "CLEAN"
    elif opts.error:
        filter_state = "ERROR"
    elif opts.empty:
        filter_state = "EMPTY"
    
    if opts.queued:
        filter_remediation = "QUEUED"
    
    # Get files
    files = db.get_files(conn, filter_state=filter_state, filter_remediation=filter_remediation, 
                        sort_by=opts.sort, order="DESC")
    
    if not files:
        print("No files found")
        conn.close()
        return
    
    # Print summary counts
    states = {}
    for f in files:
        state = f["scan_state"]
        states[state] = states.get(state, 0) + 1
    
    print("=== Summary ===")
    for state, count in sorted(states.items()):
        print(f"  {state:8s}  {count}")
    print()
    
    # Print table
    if filter_state or filter_remediation:
        print(f"{'Folder':<50} {'Size':>10} {'State':>10} {'Remediation':>12} {'Reason':<30}")
        print("-" * 120)
        for f in files:
            folder_name = Path(f["folder_path"]).name[:48]
            size_gb = f["size_bytes"] / (1024**3)
            reason = (f["stderr_tail"] or "")[:28]
            print(f"{folder_name:<50} {size_gb:>9.2f}G {f['scan_state']:>10} {f['remediation']:>12} {reason:<30}")
    
    conn.close()


def cli_queue(args):
    """CLI: repair queue"""
    parser = argparse.ArgumentParser(description="Queue files for remediation")
    parser.add_argument("--all-corrupt", action="store_true", help="Queue all CORRUPT files")
    parser.add_argument("folder", nargs="*", help="Folder name(s) to queue")
    opts = parser.parse_args(args)
    
    conn = db.init_db()
    
    if opts.all_corrupt:
        # Get all corrupt files
        files = db.get_files(conn, filter_state="CORRUPT")
        folder_paths = [f["folder_path"] for f in files]
        db.mark_queued(conn, folder_paths)
        print(f"Queued {len(folder_paths)} CORRUPT files for remediation")
    elif opts.folder:
        # Queue specific folders (partial name match)
        all_files = db.get_files(conn)
        matched = []
        for pattern in opts.folder:
            for f in all_files:
                folder_name = Path(f["folder_path"]).name
                if pattern.lower() in folder_name.lower() and f["folder_path"] not in matched:
                    matched.append(f["folder_path"])
        
        if matched:
            db.mark_queued(conn, matched)
            print(f"Queued {len(matched)} file(s):")
            for p in matched:
                print(f"  {Path(p).name}")
        else:
            print("No matching folders found")
    else:
        print("Error: specify --all-corrupt or folder name(s)")
        parser.print_help()
    
    conn.close()


def cli_remediate(args):
    """CLI: repair remediate"""
    parser = argparse.ArgumentParser(description="Execute remediation (delete + Radarr search)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    parser.add_argument("--max", type=int, help="Max files to remediate in this batch")
    opts = parser.parse_args(args)
    
    conn = db.init_db()
    
    # Get queued files
    files = db.get_files(conn, filter_remediation="QUEUED")
    
    if not files:
        print("No files queued for remediation")
        conn.close()
        return
    
    # Limit batch if specified
    if opts.max:
        files = files[:opts.max]
    
    print(f"{'[DRY RUN] ' if opts.dry_run else ''}Remediating {len(files)} file(s)...")
    
    from radarr import RadarrClient
    radarr = RadarrClient()
    
    for i, f in enumerate(files, 1):
        folder_path = f["folder_path"]
        folder_name = Path(folder_path).name
        
        print(f"\n[{i}/{len(files)}] {folder_name}")
        
        try:
            # Find movie in Radarr
            print("  Looking up in Radarr...", end=" ", flush=True)
            movie = radarr.find_movie_by_path(folder_path)
            
            if not movie:
                print("NOT FOUND")
                db.mark_failed(conn, folder_path, "Movie not found in Radarr")
                continue
            
            movie_id = movie.get("id")
            print(f"OK (id={movie_id})")
            
            # Delete file from disk
            print(f"  {'[DRY RUN] Would delete' if opts.dry_run else 'Deleting from disk'}...", end=" ", flush=True)
            if not opts.dry_run:
                if Path(folder_path).exists():
                    import shutil
                    shutil.rmtree(folder_path)
                    db.mark_deleted(conn, folder_path)
                    print("OK")
                else:
                    print("NOT FOUND")
            else:
                print("SKIP")
            
            # Radarr workflow
            moviefile = movie.get("movieFile", {})
            file_id = moviefile.get("id")
            
            if not opts.dry_run:
                print("  Unmonitoring...", end=" ", flush=True)
                radarr.unmonitor(movie_id)
                print("OK")
                
                if file_id:
                    print("  Deleting file record...", end=" ", flush=True)
                    radarr.delete_moviefile(file_id)
                    print("OK")
                
                print("  Re-monitoring...", end=" ", flush=True)
                radarr.monitor(movie_id)
                print("OK")
                
                print("  Triggering search...", end=" ", flush=True)
                cmd_id = radarr.search(movie_id)
                db.mark_researching(conn, folder_path)
                print(f"OK (cmd {cmd_id})")
            else:
                print("  [DRY RUN] Would: unmonitor → delete file record → monitor → search")
        
        except Exception as e:
            print(f"  ERROR: {e}")
            db.mark_failed(conn, folder_path, str(e))
    
    conn.close()
    print("\nDone!")


def cli_benchmark(args):
    """CLI: repair benchmark — find optimal worker count for this PC + NAS combo.

    Scans the same set of folders multiple times at different worker counts
    and reports throughput so you can see where adding workers stops helping.

    Important: STOP all other scanning activity (GUI, other CLI scans, or other
    PCs running scans on the same NAS) before running this. Concurrent traffic
    skews the results.

    The benchmark uses a temporary isolated SQLite database so it does NOT
    touch your real `repair.db` or shared Postgres state. Locks/results from
    the benchmark are discarded when it finishes.
    """
    parser = argparse.ArgumentParser(
        description="Find the optimal --workers value for your environment.",
        epilog=(
            "STOP other scanning activity first (close the GUI, pause other "
            "PCs) so the benchmark sees the full available bandwidth."
        )
    )
    parser.add_argument("--limit", type=int, default=8,
                        help="Number of folders to scan per pass (default: 8)")
    parser.add_argument("--workers", type=str, default="1,2,4,6,8",
                        help="Comma-separated worker counts to test (default: 1,2,4,6,8)")
    parser.add_argument("--root", action="append",
                        help="Library root to scan (repeatable, defaults to all from config)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the interactive confirmation about other scans")
    opts = parser.parse_args(args)

    try:
        worker_counts = [int(w.strip()) for w in opts.workers.split(",")]
    except ValueError:
        print("ERROR: --workers must be comma-separated integers, e.g. '1,2,4,8'", file=sys.stderr)
        sys.exit(1)

    if opts.root:
        roots = [Path(r) for r in opts.root]
    else:
        roots = config.get_library_roots()

    # Pre-flight warning + confirmation
    print("=" * 70)
    print(" BENCHMARK MODE")
    print("=" * 70)
    print()
    print("This will scan the same folders multiple times to measure throughput.")
    print("Estimated time: a few minutes (depends on file sizes and worker counts).")
    print()
    print("BEFORE STARTING, please stop these to avoid skewing results:")
    print("  - Close the Repair Broken Media Files GUI on THIS PC")
    print("  - Stop any other scans running on this PC")
    print("  - Stop scans on OTHER PCs that share this NAS / Postgres backend")
    print("  - Avoid heavy network usage (large downloads, streaming, etc.)")
    print()
    print("The benchmark uses a temporary isolated SQLite database, so your")
    print("real repair.db / Postgres data is NOT touched.")
    print()

    if not opts.yes:
        try:
            answer = input("Ready to start? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    # We need a clean list of folders to test.
    print()
    print("Discovering test folders...")
    candidate_folders = scanner._enumerate_movie_folders(roots)
    if not candidate_folders:
        print("ERROR: no folders found in any library root", file=sys.stderr)
        sys.exit(1)
    test_folders = candidate_folders[: opts.limit]
    print(f"Will benchmark with {len(test_folders)} folders:")
    for f in test_folders[:5]:
        print(f"  - {f.name}")
    if len(test_folders) > 5:
        print(f"  ... and {len(test_folders) - 5} more")
    print()

    # Use a temporary isolated SQLite DB so we don't pollute real state.
    # We patch db.DB_PATH and force sqlite backend for the duration.
    import tempfile
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    tmp_path = Path(tmp_db.name)

    original_db_path = db.DB_PATH
    original_backend = db.DB_BACKEND

    try:
        db.DB_PATH = tmp_path
        db.DB_BACKEND = "sqlite"

        results = []
        for wc in worker_counts:
            # Reset the temp DB between passes so each pass starts clean
            if tmp_path.exists():
                tmp_path.unlink()

            print(f"=== Pass with {wc} worker(s) ===")
            conn = db.init_db()
            start = time.time()
            stats = scanner.scan_library(
                roots=roots,
                workers=wc,
                db_conn=conn,
                progress_callback=None,
                rescan=True,  # always rescan for fair benchmark
                limit=len(test_folders),
            )
            elapsed = time.time() - start
            conn.close()

            files_done = stats.get("folders_done", 0)
            rate = files_done / elapsed if elapsed > 0 else 0
            results.append({
                "workers": wc,
                "elapsed": elapsed,
                "files_done": files_done,
                "rate": rate,
            })
            print(f"  {files_done} files in {elapsed:.1f}s = {rate:.2f} files/sec")
            print()
    finally:
        # Restore original settings and clean up the temp DB
        db.DB_PATH = original_db_path
        db.DB_BACKEND = original_backend
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

    # Summary table
    print("=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"{'Workers':>8}  {'Files':>6}  {'Time(s)':>8}  {'Rate(f/s)':>10}  {'Speedup':>8}")
    print("-" * 60)
    baseline_rate = results[0]["rate"] if results else 1.0
    if baseline_rate <= 0:
        baseline_rate = 1.0
    for r in results:
        speedup = r["rate"] / baseline_rate if baseline_rate > 0 else 0
        print(
            f"{r['workers']:>8}  {r['files_done']:>6}  "
            f"{r['elapsed']:>8.1f}  {r['rate']:>10.2f}  {speedup:>7.2f}x"
        )
    print()

    # Recommend
    if len(results) >= 2:
        best = max(results, key=lambda r: r["rate"])
        print(f"Best throughput: {best['workers']} workers ({best['rate']:.2f} files/sec)")
        # Also identify diminishing returns
        for i in range(1, len(results)):
            prev = results[i - 1]
            cur = results[i]
            if prev["rate"] > 0:
                gain = (cur["rate"] / prev["rate"]) - 1.0
                marker = "  ← good gain" if gain > 0.20 else ("  ← diminishing returns" if gain < 0.10 else "")
                print(f"  {prev['workers']} -> {cur['workers']} workers: {gain*100:+5.1f}%{marker}")
        print()
        print("Recommended: use the lowest worker count that gives you most of the speedup.")
        print("Adding more workers past that wastes CPU/network without scanning faster.")


def main():
    """Parse args and route to GUI or CLI."""
    if len(sys.argv) == 1:
        # No args -> launch GUI
        launch_gui()
    else:
        # CLI subcommands
        cmd = sys.argv[1]
        if cmd == "scan":
            cli_scan(sys.argv[2:])
        elif cmd == "list":
            cli_list(sys.argv[2:])
        elif cmd == "queue":
            cli_queue(sys.argv[2:])
        elif cmd == "remediate":
            cli_remediate(sys.argv[2:])
        elif cmd == "benchmark":
            cli_benchmark(sys.argv[2:])
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: repair [scan|list|queue|remediate|benchmark]")
            print("       repair    (no args to launch GUI)")
            sys.exit(1)


if __name__ == "__main__":
    main()
