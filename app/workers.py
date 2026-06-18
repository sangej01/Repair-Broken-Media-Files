"""QThread workers for background tasks."""
from PySide6.QtCore import QThread, Signal
from typing import Optional, List
import scanner
import db


class ScanWorker(QThread):
    """Background worker for scanning library folders."""
    
    # Signals
    discovery = Signal(int)  # total folders discovered
    scan_start = Signal(str)  # folder_path - emitted when folder scan starts
    progress = Signal(int, int, str, str)  # current, total, folder_name, state
    file_progress = Signal(str, float)  # folder_path, elapsed_sec - emitted during file scan
    result_row = Signal(dict)  # FileRecord as dict
    finished = Signal(dict)  # ScanStats as dict
    error = Signal(str)
    
    def __init__(self, roots: List, workers: int, rescan: bool = False, limit: Optional[int] = None):
        super().__init__()
        self.roots = roots
        self.workers = workers
        self.rescan = rescan
        self.limit = limit
        self._cancelled = False
        self._active_processes = []  # Track ffmpeg processes
    
    def run(self):
        """Execute scan in background thread."""
        # Create a new database connection in this thread
        thread_db_conn = None
        
        try:
            # Initialize database connection in this worker thread
            thread_db_conn = db.init_db()
            
            # Scan start callback - emitted when folder scan begins
            def scan_start_callback(folder_path):
                if self._cancelled:
                    return
                self.scan_start.emit(folder_path)
            
            # Progress callback that emits Qt signals after each folder completes
            def progress_callback(current, total, folder_path, state):
                if self._cancelled:
                    return
                self.progress.emit(current, total, folder_path, state)
            
            # File progress callback that emits during each file scan
            def file_progress_callback(folder_path, elapsed_sec):
                if self._cancelled:
                    return
                self.file_progress.emit(folder_path, elapsed_sec)
            
            # Cancel check callback
            def cancel_check():
                return self._cancelled
            
            # Note: We don't emit discovery here because we don't know the count yet.
            # The progress callback will set the maximum on first call.
            
            # Run scan with thread-local connection
            stats = scanner.scan_library(
                roots=self.roots,
                workers=self.workers,
                db_conn=thread_db_conn,
                progress_callback=progress_callback,
                file_progress_callback=file_progress_callback,
                scan_start_callback=scan_start_callback,
                cancel_flag=cancel_check,
                rescan=self.rescan,
                limit=self.limit
            )
            
            # Emit finished with stats
            if not self._cancelled:
                self.finished.emit(stats)
        
        except Exception as e:
            self.error.emit(str(e))
        
        finally:
            # Clean up thread-local connection
            if thread_db_conn:
                thread_db_conn.close()
    
    def cancel(self):
        """Request cancellation and kill any running ffmpeg processes."""
        self._cancelled = True
        # Kill ffmpeg processes via the scanner's tracked processes
        try:
            import scanner
            scanner._kill_all_active_processes()
        except Exception:
            pass


class RemediateWorker(QThread):
    """Background worker for remediation (delete + Radarr search)."""
    
    # Signals
    step = Signal(str, str, str, str)  # folder, action, status, message
    finished = Signal(dict)  # RemediationStats as dict
    error = Signal(str)
    
    def __init__(self, folder_paths: List[str], radarr_client, dry_run: bool = False, max_batch: Optional[int] = None):
        super().__init__()
        self.folder_paths = folder_paths
        self.radarr_client = radarr_client
        self.dry_run = dry_run
        self.max_batch = max_batch
        self._cancelled = False
    
    def run(self):
        """Execute remediation workflow in background thread."""
        from pathlib import Path
        import shutil
        
        # Create thread-local database connection
        thread_db_conn = db.init_db()
        
        stats = {
            "total": len(self.folder_paths),
            "processed": 0,
            "deleted": 0,
            "searched": 0,
            "failed": 0,
            "skipped": 0,
            "failures": [],  # list of (folder_name, reason) tuples
            "successes": [], # list of folder_names that were searched
        }
        
        try:
            # Limit batch if specified
            paths_to_process = self.folder_paths[:self.max_batch] if self.max_batch else self.folder_paths
            
            for folder_path in paths_to_process:
                if self._cancelled:
                    break
                
                folder_name = Path(folder_path).name
                
                try:
                    # Step 1: Find movie in Radarr
                    self.step.emit(folder_path, "lookup", "running", "Looking up in Radarr...")
                    movie = self.radarr_client.find_movie_by_path(folder_path)
                    
                    if not movie:
                        self.step.emit(folder_path, "lookup", "failed", "Movie not found in Radarr")
                        db.mark_failed(thread_db_conn, folder_path, "Movie not found in Radarr")
                        stats["failed"] += 1
                        stats["failures"].append((folder_name, "Movie not found in Radarr"))
                        stats["processed"] += 1
                        continue
                
                    movie_id = movie.get("id")
                    moviefile = movie.get("movieFile", {})
                    file_id = moviefile.get("id")
                    
                    # Step 2: Delete file from disk
                    self.step.emit(folder_path, "delete", "running", "Deleting file from disk...")
                    
                    if not self.dry_run:
                        if Path(folder_path).exists():
                            shutil.rmtree(folder_path)
                            db.mark_deleted(thread_db_conn, folder_path)
                            stats["deleted"] += 1
                        else:
                            self.step.emit(folder_path, "delete", "warning", "Folder not found on disk")
                    else:
                        self.step.emit(folder_path, "delete", "success", "[DRY RUN] Would delete")
                
                    # Step 3: Unmonitor in Radarr
                    self.step.emit(folder_path, "unmonitor", "running", "Unmonitoring in Radarr...")
                    if not self.dry_run:
                        self.radarr_client.unmonitor(movie_id)
                    else:
                        self.step.emit(folder_path, "unmonitor", "success", "[DRY RUN] Would unmonitor")
                    
                    # Step 4: Delete moviefile record if exists
                    if file_id:
                        self.step.emit(folder_path, "radarr_delete", "running", "Deleting file record in Radarr...")
                        if not self.dry_run:
                            self.radarr_client.delete_moviefile(file_id)
                        else:
                            self.step.emit(folder_path, "radarr_delete", "success", "[DRY RUN] Would delete file record")
                    
                    # Step 5: Re-monitor
                    self.step.emit(folder_path, "monitor", "running", "Re-monitoring in Radarr...")
                    if not self.dry_run:
                        self.radarr_client.monitor(movie_id)
                    else:
                        self.step.emit(folder_path, "monitor", "success", "[DRY RUN] Would monitor")
                    
                    # Step 6: Trigger search
                    self.step.emit(folder_path, "search", "running", "Triggering Radarr search...")
                    if not self.dry_run:
                        cmd_id = self.radarr_client.search(movie_id)
                        db.mark_researching(thread_db_conn, folder_path)
                        stats["searched"] += 1
                        stats["successes"].append(folder_name)
                        self.step.emit(folder_path, "search", "success", f"Search queued (cmd {cmd_id})")
                    else:
                        self.step.emit(folder_path, "search", "success", "[DRY RUN] Would trigger search")
                    
                    stats["processed"] += 1
                    
                except Exception as e:
                    error_msg = str(e)
                    self.step.emit(folder_path, "error", "failed", error_msg)
                    db.mark_failed(thread_db_conn, folder_path, error_msg)
                    stats["failed"] += 1
                    stats["failures"].append((folder_name, error_msg))
                    stats["processed"] += 1
            
            self.finished.emit(stats)
        
        finally:
            # Clean up thread-local connection
            if thread_db_conn:
                thread_db_conn.close()
    
    def cancel(self):
        """Request cancellation."""
        self._cancelled = True
