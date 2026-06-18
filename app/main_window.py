"""Main PySide6 window for Repair Broken Media Files."""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class NumericTableWidgetItem(QTableWidgetItem):
    """Table item that sorts numerically based on UserRole+1 data."""
    
    def __lt__(self, other):
        # Get the numeric value stored in UserRole+1
        self_value = self.data(Qt.ItemDataRole.UserRole + 1)
        other_value = other.data(Qt.ItemDataRole.UserRole + 1)
        
        if self_value is not None and other_value is not None:
            return self_value < other_value
        
        # Fallback to text comparison
        return super().__lt__(other)

from app.models import FileRecord
from app.workers import ScanWorker
from app.styles import DARK_THEME
import config
import db

# Table columns
COL_SELECT = 0
COL_FOLDER = 1
COL_SIZE = 2
COL_STATUS = 3      # Was VERDICT - scan status (CLEAN/CORRUPT/etc.)
COL_REASON = 4
COL_REMEDIATION = 5 # Was STATE - remediation state (NONE/QUEUED/etc.)
COL_ATTEMPTS = 6

# Backward compatibility aliases (in case any code still uses old names)
COL_VERDICT = COL_STATUS
COL_STATE = COL_REMEDIATION

HEADERS = ["", "Folder", "Size", "Status", "Reason", "Remediation", "Attempts"]

# State colors (Catppuccin Mocha palette)
STATE_COLORS = {
    "CLEAN": "#a6e3a1",     # Green
    "CORRUPT": "#f38ba8",   # Red
    "ERROR": "#f9e2af",     # Yellow
    "TIMEOUT": "#fab387",   # Orange (different from ERROR - file just took too long)
    "EMPTY": "#6c7086",     # Grey
    "MISSING": "#cba6f7",   # Purple - folder deleted/moved (no longer on disk)
    "SCANNING": "#89b4fa",  # Blue - currently being scanned (possibly by another PC)
    "REMEDIATED": "#94e2d5", # Teal
    "SKIPPED": "#585b70",   # Dark grey
}


def _size_display(size_bytes: int) -> str:
    """Format size in GB."""
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.1f}G"
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.0f}M"
    return f"{size_bytes / 1024:.0f}K"


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Repair Broken Media Files")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        
        # Apply dark theme
        self.setStyleSheet(DARK_THEME)
        
        # State
        self._files: list[FileRecord] = []
        self._worker: ScanWorker | None = None
        self._db_conn = None
        self._view_mode = "database"  # "database" or "live"
        
        # Build UI
        self._build_ui()
        
        # Setup keyboard shortcuts
        self._setup_shortcuts()
        
        # Load existing database
        self._load_db()
    
    def _build_ui(self):
        """Build the main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        
        # Heading
        heading = QLabel("Repair Broken Media Files")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        
        subtitle = QLabel(
            "Scan your movie library for structurally broken files and remediate them"
        )
        subtitle.setObjectName("subheading")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(4)
        
        # --- View Mode Toggle ---
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        
        mode_row.addWidget(QLabel("View:"))
        self._view_mode_combo = QComboBox()
        self._view_mode_combo.addItems(["Database (Show All Results)", "Live Scan (Start Fresh)"])
        self._view_mode_combo.currentTextChanged.connect(self._on_view_mode_changed)
        self._view_mode_combo.setFixedWidth(220)
        self._view_mode_combo.setToolTip(
            "Database View: Show all previously scanned files\n"
            "Live Scan: Clear table and show only current scan progress"
        )
        mode_row.addWidget(self._view_mode_combo)
        
        mode_row.addStretch()
        
        self._info_label = QLabel("💾 Showing all scanned files from database")
        self._info_label.setObjectName("subheading")
        mode_row.addWidget(self._info_label)
        
        layout.addLayout(mode_row)
        
        # --- Scan controls row ---
        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        
        scan_row.addWidget(QLabel("Library:"))
        self._lib_ah = QCheckBox("A-H")
        self._lib_ah.setChecked(True)
        scan_row.addWidget(self._lib_ah)
        
        self._lib_is = QCheckBox("I-S")
        self._lib_is.setChecked(True)
        scan_row.addWidget(self._lib_is)
        
        self._lib_tz = QCheckBox("T-Z")
        self._lib_tz.setChecked(True)
        scan_row.addWidget(self._lib_tz)
        
        scan_row.addSpacing(16)
        scan_row.addWidget(QLabel("Parallel scans:"))
        self._workers_combo = QComboBox()
        for n in range(1, 9):  # 1 through 8
            self._workers_combo.addItem(str(n))
        self._workers_combo.setCurrentText("2")
        self._workers_combo.setFixedWidth(60)
        self._workers_combo.setToolTip("Number of movies to scan simultaneously (1-8). Higher = faster but uses more CPU/disk.")
        scan_row.addWidget(self._workers_combo)
        
        scan_row.addStretch()
        
        self._scan_btn = QPushButton("Start Scan")
        self._scan_btn.setObjectName("primary")
        self._scan_btn.clicked.connect(self._start_scan)
        scan_row.addWidget(self._scan_btn)
        
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_scan)
        scan_row.addWidget(self._stop_btn)
        
        layout.addLayout(scan_row)
        
        # --- Progress row ---
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        
        self._progress_label = QLabel("Last scan: Never")
        self._progress_label.setObjectName("statusLabel")
        progress_row.addWidget(self._progress_label)
        
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(20)
        progress_row.addWidget(self._progress_bar, 1)
        
        layout.addLayout(progress_row)
        
        # --- Filter row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        
        filter_row.addWidget(QLabel("Status:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "CORRUPT", "CLEAN", "ERROR", "TIMEOUT", "EMPTY", "MISSING", "SCANNING", "UNKNOWN"])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        self._filter_combo.setFixedWidth(120)
        filter_row.addWidget(self._filter_combo)
        
        filter_row.addSpacing(16)
        filter_row.addWidget(QLabel("Remediation:"))
        self._remed_combo = QComboBox()
        self._remed_combo.addItems(["Any", "NONE", "QUEUED", "DELETED", "REMEDIATED", "SKIPPED"])
        self._remed_combo.currentTextChanged.connect(self._apply_filter)
        self._remed_combo.setFixedWidth(120)
        filter_row.addWidget(self._remed_combo)
        
        filter_row.addSpacing(16)
        filter_row.addWidget(QLabel("Search:"))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter by folder name...")
        self._search_box.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search_box, 1)
        
        layout.addLayout(filter_row)
        
        # --- Table ---
        self._table = QTableWidget()
        self._table.setColumnCount(len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.setSortingEnabled(True)  # Enable column sorting
        
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_SELECT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_FOLDER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_VERDICT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_REASON, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_ATTEMPTS, QHeaderView.ResizeMode.ResizeToContents)
        
        self._table.setColumnWidth(COL_SELECT, 40)
        
        layout.addWidget(self._table, 1)
        
        # --- Bottom status bar ---
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        
        self._status_label = QLabel("Ready")
        self._status_label.setObjectName("summary")
        status_row.addWidget(self._status_label)
        
        layout.addLayout(status_row)
        
        # --- Action buttons row ---
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._select_all)
        action_row.addWidget(self._select_all_btn)
        
        self._select_none_btn = QPushButton("Select None")
        self._select_none_btn.clicked.connect(self._select_none)
        action_row.addWidget(self._select_none_btn)
        
        action_row.addStretch()
        
        self._queue_btn = QPushButton("Queue for Remediation")
        self._queue_btn.clicked.connect(self._queue_selected)
        action_row.addWidget(self._queue_btn)
        
        self._remediate_btn = QPushButton("Delete + Re-search")
        self._remediate_btn.setObjectName("danger")
        self._remediate_btn.clicked.connect(self._remediate_queued)
        action_row.addWidget(self._remediate_btn)
        
        self._open_folder_btn = QPushButton("Open Folder")
        self._open_folder_btn.clicked.connect(self._open_folder)
        action_row.addWidget(self._open_folder_btn)
        
        self._show_log_btn = QPushButton("Show ffmpeg Log")
        self._show_log_btn.clicked.connect(self._show_log)
        action_row.addWidget(self._show_log_btn)
        
        layout.addLayout(action_row)
    
    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Ctrl+W or Ctrl+Q to quit
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.close)
        
        quit_shortcut2 = QShortcut(QKeySequence("Ctrl+W"), self)
        quit_shortcut2.activated.connect(self.close)
        
        # Ctrl+R to refresh table
        refresh_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        refresh_shortcut.activated.connect(self._refresh_table)
        
        # Ctrl+F to focus search box
        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(lambda: self._search_box.setFocus())
        
        # Escape to stop scan
        escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        escape_shortcut.activated.connect(self._stop_scan)
    
    def _load_db(self):
        """Load existing database and populate table."""
        self._db_conn = db.init_db()
        # Start in database view mode - show all existing results
        self._view_mode = "database"
        self._refresh_table()
    
    def _refresh_table(self):
        """Refresh table from database, respecting view mode and filters."""
        if not self._db_conn:
            return
        
        # Get filter values (apply in both modes)
        filter_state = None if self._filter_combo.currentText() == "All" else self._filter_combo.currentText()
        filter_remed = None if self._remed_combo.currentText() == "Any" else self._remed_combo.currentText()
        search = self._search_box.text().lower()
        
        if self._view_mode == "live":
            # In live mode: only show files scanned in CURRENT scan session
            # Use the tracked set of folder paths from current scan
            if not hasattr(self, '_live_scan_paths'):
                self._live_scan_paths = set()
            
            if not self._live_scan_paths:
                # No scan results yet, just clear and update counts
                self._table.setRowCount(0)
                self._update_status_counts()
                return
            
            # Get only files from current scan session
            all_files = db.get_files(self._db_conn, filter_state=filter_state, filter_remediation=filter_remed)
            files = [f for f in all_files if f["folder_path"] in self._live_scan_paths]
        else:
            # Database mode: show all files
            files = db.get_files(self._db_conn, filter_state=filter_state, filter_remediation=filter_remed)
        
        # Apply search filter (works in both modes)
        if search:
            files = [f for f in files if search in Path(f["folder_path"]).name.lower()]
        
        # Update table
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for file_dict in files:
            self._add_file_row(file_dict)
        
        # Re-enable sorting
        self._table.setSortingEnabled(True)
        
        # Update status counts
        self._update_status_counts()
        
        # Update status label
        self._update_status_counts()
    
    def _update_status_counts(self):
        """Update the status label with counts from database."""
        if not self._db_conn:
            return
        
        counts = {}
        all_files = db.get_files(self._db_conn)
        for f in all_files:
            state = f["scan_state"]
            counts[state] = counts.get(state, 0) + 1
        
        status_parts = [f"{len(all_files)} total"]
        if counts.get("CORRUPT", 0) > 0:
            status_parts.append(f"{counts['CORRUPT']} corrupt")
        if counts.get("CLEAN", 0) > 0:
            status_parts.append(f"{counts['CLEAN']} clean")
        if counts.get("ERROR", 0) > 0:
            status_parts.append(f"{counts['ERROR']} error")
        if counts.get("EMPTY", 0) > 0:
            status_parts.append(f"{counts['EMPTY']} empty")
        
        self._status_label.setText(", ".join(status_parts))
    
    def _add_file_row(self, file_dict: dict):
        """Add a file row to the table."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        
        # Checkbox
        checkbox = QCheckBox()
        cell_widget = QWidget()
        layout = QHBoxLayout(cell_widget)
        layout.addWidget(checkbox)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table.setCellWidget(row, COL_SELECT, cell_widget)
        
        # Folder
        folder_name = Path(file_dict["folder_path"]).name
        folder_item = QTableWidgetItem(folder_name)
        self._table.setItem(row, COL_FOLDER, folder_item)
        
        # Size (use custom numeric sort)
        size_item = NumericTableWidgetItem(_size_display(file_dict["size_bytes"]))
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Store raw bytes for proper numeric sorting
        size_item.setData(Qt.ItemDataRole.UserRole + 1, file_dict["size_bytes"])
        self._table.setItem(row, COL_SIZE, size_item)
        
        # Verdict
        verdict = file_dict["scan_state"]
        verdict_item = QTableWidgetItem(verdict)
        verdict_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if verdict in STATE_COLORS:
            verdict_item.setForeground(QColor(STATE_COLORS[verdict]))
        
        # Make CORRUPT rows more visible
        if verdict == "CORRUPT":
            # Bold font for corrupt files
            font = verdict_item.font()
            font.setBold(True)
            verdict_item.setFont(font)
            folder_item.setFont(font)
        
        self._table.setItem(row, COL_VERDICT, verdict_item)
        
        # Reason (stderr tail)
        reason = (file_dict.get("stderr_tail") or "")[:60]
        reason_item = QTableWidgetItem(reason)
        self._table.setItem(row, COL_REASON, reason_item)
        
        # Remediation state
        remed = file_dict["remediation"]
        remed_item = QTableWidgetItem(remed)
        remed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, COL_STATE, remed_item)
        
        # Attempts (use numeric sort)
        attempts = file_dict.get("attempts", 0) or 0
        attempts_item = NumericTableWidgetItem(str(attempts))
        attempts_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        attempts_item.setData(Qt.ItemDataRole.UserRole + 1, attempts)
        # Highlight if multiple attempts (indicates persistent issue)
        if attempts >= 2:
            font = attempts_item.font()
            font.setBold(True)
            attempts_item.setFont(font)
            attempts_item.setForeground(QColor("#fab387"))  # Orange warning
        if attempts >= 3:
            attempts_item.setForeground(QColor("#f38ba8"))  # Red - serious!
        self._table.setItem(row, COL_ATTEMPTS, attempts_item)
        
        # Store full path in user data
        folder_item.setData(Qt.ItemDataRole.UserRole, file_dict["folder_path"])
    
    @Slot()
    def _apply_filter(self):
        """Apply filters to table."""
        self._refresh_table()
    
    @Slot()
    def _on_view_mode_changed(self, mode: str):
        """Handle view mode change."""
        if mode.startswith("Database"):
            self._view_mode = "database"
            self._info_label.setText("💾 Showing all scanned files from database (filters apply)")
            # Disable scan controls in database view
            self._lib_ah.setEnabled(False)
            self._lib_is.setEnabled(False)
            self._lib_tz.setEnabled(False)
            self._workers_combo.setEnabled(False)
            self._scan_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            # Load all results from database
            self._refresh_table()
        else:  # Live Scan Mode
            self._view_mode = "live"
            self._info_label.setText("🔴 Live scan mode - table starts empty, populates as scan runs")
            # Enable scan controls
            self._lib_ah.setEnabled(True)
            self._lib_is.setEnabled(True)
            self._lib_tz.setEnabled(True)
            self._workers_combo.setEnabled(True)
            self._scan_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            # Clear live scan tracking and table
            self._live_scan_paths = set()
            self._table.setRowCount(0)
            self._status_label.setText("Ready to scan")
            self._progress_bar.setValue(0)
    
    @Slot()
    def _start_scan(self):
        """Start library scan."""
        # Switch to live mode if not already
        if self._view_mode == "database":
            self._view_mode_combo.setCurrentText("Live Scan (Start Fresh)")
            return  # Will trigger mode change which clears table, then user clicks Start Scan again
        
        # Table is already cleared when switching to Live mode
        
        # Get selected library roots
        roots = []
        default_roots = config.get_library_roots()
        if self._lib_ah.isChecked() and len(default_roots) > 0:
            roots.append(default_roots[0])
        if self._lib_is.isChecked() and len(default_roots) > 1:
            roots.append(default_roots[1])
        if self._lib_tz.isChecked() and len(default_roots) > 2:
            roots.append(default_roots[2])
        
        if not roots:
            QMessageBox.warning(self, "No Library Selected", "Please select at least one library to scan")
            return
        
        workers = int(self._workers_combo.currentText())
        
        # Disable scan controls during scan (but leave action buttons enabled)
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._lib_ah.setEnabled(False)
        self._lib_is.setEnabled(False)
        self._lib_tz.setEnabled(False)
        self._workers_combo.setEnabled(False)
        
        # Reset progress bar to definite mode (no pulsing)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Starting...")
        
        # Start worker (worker will create its own DB connection)
        self._worker = ScanWorker(roots, workers, rescan=False, limit=None)
        self._worker.discovery.connect(self._on_discovery)
        self._worker.scan_start.connect(self._on_scan_start)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.result_row.connect(self._on_result_row)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()
    
    @Slot()
    def _stop_scan(self):
        """Stop the current scan."""
        if not self._worker:
            return
        
        # Disconnect signals FIRST to prevent late signal processing during shutdown
        try:
            self._worker.discovery.disconnect()
            self._worker.scan_start.disconnect()
            self._worker.progress.disconnect()
            self._worker.file_progress.disconnect()
            self._worker.result_row.disconnect()
            self._worker.finished.disconnect()
            self._worker.error.disconnect()
        except Exception:
            pass
        
        # Set cancel flag - this kills ffmpeg and signals scanner to stop
        self._worker.cancel()
        
        # Wait for graceful shutdown (scanner should return immediately on cancel)
        if not self._worker.wait(5000):
            self._worker.terminate()
            self._worker.wait(2000)
        
        # Final cleanup of any lingering ffmpeg
        try:
            self._kill_ffmpeg_processes()
        except Exception:
            pass
        
        # Reset UI - stop the pulsating animation by setting a definite range
        self._progress_label.setText("Scan stopped")
        self._progress_bar.setRange(0, 100)  # Definite range stops pulsing
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Stopped")
        self._stop_btn.setEnabled(False)
        self._worker = None
        
        # Re-enable scan controls based on current view mode
        if self._view_mode == "live":
            self._scan_btn.setEnabled(True)
            self._lib_ah.setEnabled(True)
            self._lib_is.setEnabled(True)
            self._lib_tz.setEnabled(True)
            self._workers_combo.setEnabled(True)
    
    def _kill_ffmpeg_processes(self):
        """Kill any ffmpeg processes that may be orphaned."""
        try:
            # Use PowerShell to safely kill ffmpeg processes
            # More reliable than taskkill and won't crash the app
            cmd = 'Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue'
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=3
            )
        except:
            # If PowerShell fails, silently continue - not critical
            pass
    
    @Slot(int)
    def _on_discovery(self, total: int):
        """Handle discovery signal."""
        self._progress_label.setText(f"Discovered {total} folders")
        # Avoid setting maximum to 0 which triggers indeterminate (pulsating) mode
        if total > 0:
            self._progress_bar.setMaximum(total)
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
    
    @Slot(str)
    def _on_scan_start(self, folder_path: str):
        """Handle scan start - add row immediately with SCANNING state."""
        # Only add row in live mode
        if self._view_mode != "live":
            return
        
        # Track this path as part of current scan session
        if not hasattr(self, '_live_scan_paths'):
            self._live_scan_paths = set()
        self._live_scan_paths.add(folder_path)
        
        # Add placeholder row to table
        row_data = {
            "folder_path": folder_path,
            "video_path": None,
            "size_bytes": 0,
            "scan_state": "SCANNING",
            "stderr_tail": "",
            "remediation": "NONE",
            "radarr_movie_id": None,
            "radarr_tmdb_id": None,
            "last_scan_at": None,
            "last_scan_secs": None,
            "remediation_at": None,
            "remediation_log": None,
            "attempts": 0,
            "first_seen_at": None,
            "notes": None,
        }
        self._add_file_row(row_data)
    
    @Slot(str, float)
    def _on_file_progress(self, folder_path: str, elapsed_sec: float):
        """Handle per-file progress update during scan."""
        # Ignore late signals when no worker
        if self._worker is None:
            return
        
        folder_name = Path(folder_path).name if folder_path else ""
        minutes = int(elapsed_sec // 60)
        seconds = int(elapsed_sec % 60)
        self._progress_label.setText(f"⏱ Scanning: {folder_name} ({minutes}m {seconds:02d}s)")
        
        # Only update rows in live mode
        if self._view_mode != "live":
            return
        
        # Normalize path for comparison
        normalized_path = str(Path(folder_path))
        
        # Update the row if it exists
        for row in range(self._table.rowCount()):
            folder_item = self._table.item(row, COL_FOLDER)
            if folder_item:
                row_path = folder_item.data(Qt.ItemDataRole.UserRole)
                if row_path and str(Path(row_path)) == normalized_path:
                    # Update the reason column with elapsed time
                    reason_item = self._table.item(row, COL_REASON)
                    if reason_item:
                        reason_item.setText(f"Scanning... {minutes}m {seconds:02d}s")
                    break
    
    @Slot(int, int, str, str)
    def _on_progress(self, current: int, total: int, folder_path: str, state: str):
        """Handle progress update after folder completes."""
        # Ignore late signals when no worker
        if self._worker is None:
            return
        
        folder_name = Path(folder_path).name if folder_path else ""
        
        # Show state-specific message
        if state == "CORRUPT":
            status = "⚠ CORRUPT"
        elif state == "CLEAN":
            status = "✓ Clean"
        elif state == "ERROR":
            status = "✗ Error"
        elif state == "EMPTY":
            status = "○ Empty"
        else:
            status = "Scanning"
        
        self._progress_label.setText(f"{status}: {folder_name}")
        self._progress_bar.setValue(current)
        self._progress_bar.setFormat(f"{current}/{total} ({100*current//total if total > 0 else 0}%)")
        
        # In live mode, update just the existing row in place (don't refresh whole table)
        if self._view_mode == "live":
            self._update_row_state(folder_path, state)
            self._update_status_counts()
    
    def _update_row_state(self, folder_path: str, state: str):
        """Update a specific row's state without refreshing the whole table."""
        # Normalize path for comparison
        normalized_path = str(Path(folder_path))
        
        # Get the file from DB to get full record
        files = db.get_files(self._db_conn)
        file_record = next(
            (f for f in files if str(Path(f["folder_path"])) == normalized_path),
            None
        )
        
        if not file_record:
            return
        
        # Find and update the row using normalized path comparison
        for row in range(self._table.rowCount()):
            folder_item = self._table.item(row, COL_FOLDER)
            if not folder_item:
                continue
            
            row_path = folder_item.data(Qt.ItemDataRole.UserRole)
            if not row_path or str(Path(row_path)) != normalized_path:
                continue
            
            # Match found - update this row
            # Update size
            size_item = self._table.item(row, COL_SIZE)
            if size_item:
                size_item.setText(_size_display(file_record["size_bytes"]))
                size_item.setData(Qt.ItemDataRole.UserRole + 1, file_record["size_bytes"])
            
            # Update verdict
            verdict_item = self._table.item(row, COL_VERDICT)
            if verdict_item:
                verdict_item.setText(state)
                if state in STATE_COLORS:
                    verdict_item.setForeground(QColor(STATE_COLORS[state]))
                if state == "CORRUPT":
                    font = verdict_item.font()
                    font.setBold(True)
                    verdict_item.setFont(font)
                    folder_item.setFont(font)
            
            # Update reason
            reason_item = self._table.item(row, COL_REASON)
            if reason_item:
                reason = (file_record.get("stderr_tail") or "")[:60]
                reason_item.setText(reason)
            
            # Update attempts
            attempts_item = self._table.item(row, COL_ATTEMPTS)
            if attempts_item:
                attempts = file_record.get("attempts", 0) or 0
                attempts_item.setText(str(attempts))
                attempts_item.setData(Qt.ItemDataRole.UserRole + 1, attempts)
                if attempts >= 2:
                    font = attempts_item.font()
                    font.setBold(True)
                    attempts_item.setFont(font)
                    attempts_item.setForeground(QColor("#fab387"))
                if attempts >= 3:
                    attempts_item.setForeground(QColor("#f38ba8"))
            
            break
    
    @Slot(dict)
    def _on_result_row(self, file_dict: dict):
        """Handle new result row."""
        # In live mode, _on_progress already updates rows in place
        # In database mode, this signal isn't typically emitted during scan
        # So we don't need to refresh here
        pass
    
    @Slot(dict)
    def _on_scan_finished(self, stats: dict):
        """Handle scan completion."""
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._lib_ah.setEnabled(True)
        self._lib_is.setEnabled(True)
        self._lib_tz.setEnabled(True)
        self._workers_combo.setEnabled(True)
        
        # Clean up worker
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        
        # Show summary
        msg = (
            f"Scan complete!\n\n"
            f"Folders scanned: {stats['folders_done']}\n"
            f"CLEAN: {stats['clean_count']}\n"
            f"CORRUPT: {stats['corrupt_count']}\n"
            f"ERROR: {stats['error_count']}\n"
            f"EMPTY: {stats['empty_count']}"
        )
        self._progress_label.setText("Scan complete - switch to Database View to see all results")
        self._progress_bar.setValue(stats['folders_done'])
        
        # Re-enable sorting after scan
        if self._view_mode == "database":
            self._table.setSortingEnabled(True)
        
        QMessageBox.information(self, "Scan Complete", msg)
    
    @Slot(str)
    def _on_error(self, error_msg: str):
        """Handle error."""
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        QMessageBox.critical(self, "Error", f"Scan error: {error_msg}")
    
    @Slot()
    def _select_all(self):
        """Select all visible rows."""
        for row in range(self._table.rowCount()):
            widget = self._table.cellWidget(row, COL_SELECT)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)
    
    @Slot()
    def _select_none(self):
        """Deselect all rows."""
        for row in range(self._table.rowCount()):
            widget = self._table.cellWidget(row, COL_SELECT)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)
    
    @Slot()
    def _queue_selected(self):
        """Queue selected files for remediation."""
        selected_paths = []
        for row in range(self._table.rowCount()):
            widget = self._table.cellWidget(row, COL_SELECT)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    folder_item = self._table.item(row, COL_FOLDER)
                    if folder_item:
                        path = folder_item.data(Qt.ItemDataRole.UserRole)
                        selected_paths.append(path)
        
        if not selected_paths:
            QMessageBox.warning(self, "No Selection", "Please select files to queue")
            return
        
        db.mark_queued(self._db_conn, selected_paths)
        QMessageBox.information(self, "Queued", f"Queued {len(selected_paths)} file(s) for remediation")
        self._refresh_table()
    
    @Slot()
    def _open_folder(self):
        """Open selected folder in Explorer."""
        current_row = self._table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "No Selection", "Please select a file")
            return
        
        folder_item = self._table.item(current_row, COL_FOLDER)
        if folder_item:
            path = folder_item.data(Qt.ItemDataRole.UserRole)
            if os.path.exists(path):
                if os.name == "nt":
                    os.startfile(path)
                else:
                    subprocess.run(["xdg-open", path])
    
    @Slot()
    def _show_log(self):
        """Show ffmpeg stderr log for selected file."""
        current_row = self._table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "No Selection", "Please select a file")
            return
        
        folder_item = self._table.item(current_row, COL_FOLDER)
        if folder_item:
            path = folder_item.data(Qt.ItemDataRole.UserRole)
            
            # Get full record from database
            files = db.get_files(self._db_conn)
            file_record = next((f for f in files if f["folder_path"] == path), None)
            
            if file_record:
                log = file_record.get("stderr_tail") or "No log available"
                QMessageBox.information(
                    self, 
                    f"ffmpeg Log - {Path(path).name}", 
                    log
                )
    
    @Slot()
    def _show_context_menu(self, position):
        """Show right-click context menu on table."""
        current_row = self._table.currentRow()
        if current_row < 0:
            return
        
        menu = QMenu(self)
        
        # Get the file info
        folder_item = self._table.item(current_row, COL_FOLDER)
        if not folder_item:
            return
        
        path = folder_item.data(Qt.ItemDataRole.UserRole)
        verdict_item = self._table.item(current_row, COL_VERDICT)
        verdict = verdict_item.text() if verdict_item else ""
        
        # Actions
        open_action = menu.addAction("📁 Open Folder")
        open_action.triggered.connect(self._open_folder)
        
        log_action = menu.addAction("📄 Show ffmpeg Log")
        log_action.triggered.connect(self._show_log)
        
        menu.addSeparator()
        
        # Get current remediation state
        state_item = self._table.item(current_row, COL_STATE)
        remed_state = state_item.text() if state_item else "NONE"
        
        # Queue/unqueue actions based on current state
        if verdict == "CORRUPT" and remed_state == "NONE":
            queue_action = menu.addAction("➕ Queue for Remediation")
            queue_action.triggered.connect(lambda: self._queue_single(path))
            menu.addSeparator()
        elif remed_state == "QUEUED":
            unqueue_action = menu.addAction("➖ Remove from Queue")
            unqueue_action.triggered.connect(lambda: self._unqueue_single(path))
            menu.addSeparator()
        
        # Mark as Skipped (for any state)
        skip_action = menu.addAction("🚫 Mark as Skipped")
        skip_action.triggered.connect(lambda: self._skip_single(path))
        
        menu.addSeparator()
        
        # Verify folder exists
        verify_action = menu.addAction("🔍 Verify Folder Exists")
        verify_action.triggered.connect(lambda: self._verify_single(path))
        
        # Delete record from database (only for MISSING)
        if remed_state != "DELETING" and (verdict == "MISSING" or remed_state in ("FAILED", "SKIPPED")):
            delete_record_action = menu.addAction("🗑️ Delete from SQLite Database")
            delete_record_action.triggered.connect(lambda: self._delete_record_single(path))
        
        menu.addSeparator()
        
        # Copy path
        copy_action = menu.addAction("📋 Copy Path")
        copy_action.triggered.connect(lambda: self._copy_path(path))
        
        menu.exec(self._table.viewport().mapToGlobal(position))
    
    @Slot()
    def _queue_single(self, path: str):
        """Queue a single file for remediation."""
        db.mark_queued(self._db_conn, [path])
        self._refresh_table()
        folder_name = Path(path).name
        self._progress_label.setText(f"Queued: {folder_name}")
    
    @Slot()
    def _unqueue_single(self, path: str):
        """Remove a single file from the queue (back to NONE)."""
        db.mark_none(self._db_conn, path)
        self._refresh_table()
        folder_name = Path(path).name
        self._progress_label.setText(f"Removed from queue: {folder_name}")
    
    @Slot()
    def _skip_single(self, path: str):
        """Mark a single file as skipped."""
        db.mark_skipped(self._db_conn, path)
        self._refresh_table()
        folder_name = Path(path).name
        self._progress_label.setText(f"Skipped: {folder_name}")
    
    @Slot()
    def _verify_single(self, path: str):
        """Check if a single folder still exists on disk."""
        folder_name = Path(path).name
        if Path(path).exists():
            QMessageBox.information(self, "Folder Exists", f"✓ {folder_name}\n\nFolder exists on disk.")
        else:
            reply = QMessageBox.question(
                self,
                "Folder Missing",
                f"✗ {folder_name}\n\nFolder no longer exists on disk.\n\nMark it as MISSING in the database?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                db.mark_missing(self._db_conn, path)
                self._refresh_table()
                self._progress_label.setText(f"Marked missing: {folder_name}")
    
    @Slot()
    def _delete_record_single(self, path: str):
        """Permanently delete a database record."""
        folder_name = Path(path).name
        reply = QMessageBox.question(
            self,
            "Delete from SQLite Database",
            f"⚠️ Permanently delete record from this tool's local SQLite database:\n\n"
            f"{folder_name}\n\n"
            f"This affects ONLY repair.db (this tool's tracking database).\n"
            f"It does NOT touch:\n"
            f"  • Files on disk\n"
            f"  • Radarr database\n"
            f"  • Any other tool\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.delete_record(self._db_conn, path)
            self._refresh_table()
            self._progress_label.setText(f"Deleted record: {folder_name}")
    
    @Slot()
    def _copy_path(self, path: str):
        """Copy folder path to clipboard."""
        from PySide6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(path)
        folder_name = Path(path).name
        self._progress_label.setText(f"Copied path: {folder_name}")
    
    @Slot()
    def _remediate_queued(self):
        """Execute remediation on queued files."""
        from radarr import RadarrClient
        from app.workers import RemediateWorker
        
        # Get queued files
        queued = db.get_files(self._db_conn, filter_remediation="QUEUED")
        
        if not queued:
            QMessageBox.warning(self, "No Files Queued", "No files are queued for remediation")
            return
        
        # Confirm
        reply = QMessageBox.question(
            self,
            "Confirm Remediation",
            f"This will:\n"
            f"1. Delete {len(queued)} file(s) from disk\n"
            f"2. Tell Radarr to re-search for them\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Extract folder paths
        folder_paths = [f["folder_path"] for f in queued]
        
        # Create Radarr client
        try:
            radarr = RadarrClient()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to connect to Radarr: {e}")
            return
        
        # Start remediation worker (worker will create its own DB connection)
        self._remediate_worker = RemediateWorker(
            folder_paths=folder_paths,
            radarr_client=radarr,
            dry_run=False,
            max_batch=None
        )
        
        self._remediate_worker.step.connect(self._on_remediate_step)
        self._remediate_worker.finished.connect(self._on_remediate_finished)
        self._remediate_worker.error.connect(self._on_error)
        
        # Disable buttons during remediation
        self._remediate_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        
        self._remediate_worker.start()
    
    @Slot(str, str, str, str)
    def _on_remediate_step(self, folder_path: str, action: str, status: str, message: str):
        """Handle remediation step update."""
        folder_name = Path(folder_path).name
        self._progress_label.setText(f"{folder_name}: {action} - {message}")
    
    @Slot(dict)
    def _on_remediate_finished(self, stats: dict):
        """Handle remediation completion."""
        self._remediate_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        
        # Build summary message
        summary_lines = [
            "Remediation complete!",
            "",
            f"Processed: {stats['processed']}",
            f"Deleted: {stats['deleted']}",
            f"Searched: {stats['searched']}",
            f"Failed: {stats['failed']}",
        ]
        
        # Add successful remediations
        successes = stats.get("successes", [])
        if successes:
            summary_lines.append("")
            summary_lines.append("✓ Successfully remediated:")
            for name in successes[:10]:  # Show up to 10
                summary_lines.append(f"  • {name}")
            if len(successes) > 10:
                summary_lines.append(f"  ... and {len(successes) - 10} more")
        
        # Add failure details
        failures = stats.get("failures", [])
        if failures:
            summary_lines.append("")
            summary_lines.append("✗ Failures:")
            for name, reason in failures[:10]:  # Show up to 10
                summary_lines.append(f"  • {name}")
                summary_lines.append(f"    Reason: {reason}")
            if len(failures) > 10:
                summary_lines.append(f"  ... and {len(failures) - 10} more (see Database View)")
        
        msg = "\n".join(summary_lines)
        
        # Use a custom message box that allows for longer text
        from PySide6.QtWidgets import QMessageBox
        msgbox = QMessageBox(self)
        msgbox.setWindowTitle("Remediation Complete")
        msgbox.setIcon(QMessageBox.Icon.Information if stats['failed'] == 0 else QMessageBox.Icon.Warning)
        msgbox.setText(msg)
        msgbox.exec()
        
        # Refresh table
        self._refresh_table()
        
        # Clean up remediation worker
        if hasattr(self, '_remediate_worker') and self._remediate_worker:
            self._remediate_worker.deleteLater()
            self._remediate_worker = None
    
    def closeEvent(self, event):
        """Handle window close."""
        # Check if any workers are running
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Scan in Progress",
                "A scan is currently running. Stop it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self._worker.cancel()
                self._worker.terminate()
                self._worker.wait(3000)  # Wait up to 3 seconds
                self._kill_ffmpeg_processes()  # Kill orphaned ffmpeg
                self._worker.deleteLater()
                self._worker = None
            else:
                event.ignore()
                return
        
        # Check for remediation worker
        if hasattr(self, '_remediate_worker') and self._remediate_worker and self._remediate_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Remediation in Progress",
                "Remediation is currently running. Stop it and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self._remediate_worker.cancel()
                self._remediate_worker.terminate()
                self._remediate_worker.wait(3000)
                self._remediate_worker.deleteLater()
                self._remediate_worker = None
            else:
                event.ignore()
                return
        
        # Close database connection
        if self._db_conn:
            self._db_conn.close()
        
        event.accept()
