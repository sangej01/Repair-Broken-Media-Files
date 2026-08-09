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
import scanner

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

# Special status-filter label that matches several real states at once.
# These are the scan states worth re-examining: the scan either never reached
# a real verdict (UNKNOWN = interrupted, TIMEOUT = ran out of time) or couldn't
# run at all (ERROR = ffmpeg/exec/stat failure). Re-scanning any of these can
# legitimately produce a different outcome.
PROBLEMATIC_FILTER_LABEL = "Problematic"
PROBLEMATIC_STATES = {"TIMEOUT", "UNKNOWN", "ERROR"}

# Remediation states meaning the original file has been deleted and a fresh
# download requested from Radarr. Rows in these states are shown grayed-out and
# italicized: the listed original is gone / being replaced.
REMEDIATED_REMEDIATION_STATES = {"DELETED", "RESEARCHING", "REMEDIATED"}
# Muted foreground for grayed-out remediated rows (Catppuccin "overlay0").
REMEDIATED_ROW_COLOR = "#6c7086"

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


# Per-file ffmpeg timeout options (label -> seconds; 0 means no limit)
_TIMEOUT_MAP = {
    "30 min":   1800,
    "1 hr":     3600,
    "2 hr":     7200,
    "4 hr":    14400,
    "8 hr":    28800,
    "No limit":    0,
}


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Repair Broken Media Files  v{config.APP_VERSION}")
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
            f"Scan your movie library for structurally broken files and remediate them"
            f"   ·   v{config.APP_VERSION}"
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
        
        scan_row.addSpacing(16)
        scan_row.addWidget(QLabel("Timeout/file:"))
        self._timeout_combo = QComboBox()
        for label in _TIMEOUT_MAP:
            self._timeout_combo.addItem(label)
        self._timeout_combo.setCurrentText("30 min")
        self._timeout_combo.setFixedWidth(90)
        self._timeout_combo.setToolTip(
            "Per-file ffmpeg timeout. Increase when scanning over a slow network connection."
        )
        scan_row.addWidget(self._timeout_combo)
        
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
        
        # --- Per-worker activity panel ---
        # One line per concurrently-scanning file (movie name + live timer), so
        # with N parallel workers you see all N files in flight, not just one.
        self._worker_panel = QVBoxLayout()
        self._worker_panel.setSpacing(2)
        self._worker_panel.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._worker_panel)
        # folder_path -> QLabel for its progress line
        self._worker_rows: dict = {}
        
        # --- Filter row ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        
        _status_lbl = QLabel("Status:")
        _status_tip = (
            "Filter by SCAN VERDICT (the file's condition): CLEAN, CORRUPT, "
            "TIMEOUT, ERROR, EMPTY, MISSING, SCANNING, UNKNOWN.\n"
            "'Problematic' = TIMEOUT + UNKNOWN + ERROR (worth re-scanning)."
        )
        _status_lbl.setToolTip(_status_tip)
        filter_row.addWidget(_status_lbl)
        self._filter_combo = QComboBox()
        self._filter_combo.setToolTip(_status_tip)
        # "All" and the aggregate "Problematic" shortcut sit above a dotted
        # separator; the individual scan states sit below it.
        self._filter_combo.addItems(["All", PROBLEMATIC_FILTER_LABEL])
        self._filter_combo.insertSeparator(self._filter_combo.count())
        self._filter_combo.addItems(
            ["CORRUPT", "CLEAN", "ERROR", "TIMEOUT", "EMPTY", "MISSING", "SCANNING", "UNKNOWN"]
        )
        self._filter_combo.setItemData(
            self._filter_combo.findText(PROBLEMATIC_FILTER_LABEL),
            "Show all files worth re-scanning: TIMEOUT, UNKNOWN and ERROR "
            "(scans that timed out, were interrupted, or failed to run).",
            Qt.ItemDataRole.ToolTipRole,
        )
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        self._filter_combo.setFixedWidth(120)
        filter_row.addWidget(self._filter_combo)
        
        filter_row.addSpacing(16)
        _remed_lbl = QLabel("Remediation:")
        _remed_tip = (
            "Filter by REMEDIATION STATE (what you've done about it): NONE, "
            "QUEUED, DELETED, RESEARCHING, REMEDIATED, FAILED, SKIPPED.\n"
            "This is independent of Status — a file can be CORRUPT yet already "
            "QUEUED or RESEARCHING."
        )
        _remed_lbl.setToolTip(_remed_tip)
        filter_row.addWidget(_remed_lbl)
        self._remed_combo = QComboBox()
        self._remed_combo.setToolTip(_remed_tip)
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
        
        # Toggle to hide/show files you've marked SKIPPED (Remediation = SKIPPED).
        # Applies immediately, in any view, scanning or not.
        self._show_skipped = True
        self._skip_toggle_btn = QPushButton("Hide Skipped")
        self._skip_toggle_btn.setToolTip(
            "Hide rows whose Remediation is SKIPPED (files you chose to leave "
            "alone via 'Mark as Skipped'). Click again to show them."
        )
        self._skip_toggle_btn.clicked.connect(self._toggle_show_skipped)
        filter_row.addWidget(self._skip_toggle_btn)
        
        layout.addLayout(filter_row)
        
        # --- Table ---
        self._table = QTableWidget()
        self._table.setColumnCount(len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        # Explain the two easily-confused columns right in the header tooltips.
        self._set_header_tooltip(
            COL_STATUS,
            "STATUS = the scan verdict (the file's condition):\n"
            "CLEAN, CORRUPT, TIMEOUT, ERROR, EMPTY, MISSING, SCANNING, UNKNOWN.\n"
            "Set by the scanner. Answers: 'Is this file OK?'"
        )
        self._set_header_tooltip(
            COL_REASON,
            "REASON = the ffmpeg detail behind a non-CLEAN status,\n"
            "prefixed with a triage label (e.g. [Incomplete / truncated]).\n"
            "Hover a cell for the full explanation."
        )
        self._set_header_tooltip(
            COL_REMEDIATION,
            "REMEDIATION = what YOU have done about it (the fix-it workflow):\n"
            "NONE, QUEUED, DELETED, RESEARCHING, REMEDIATED, FAILED, SKIPPED.\n"
            "Independent of Status. Answers: 'Where is this in the fix pipeline?'"
        )
        self._set_header_tooltip(
            COL_ATTEMPTS,
            "ATTEMPTS = how many times this folder has been remediated.\n"
            "Orange at 2, red at 3+ — repeated attempts signal a systemic issue."
        )
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
        status_choice = self._filter_combo.currentText()
        # "Problematic" is an aggregate of several states, so it can't be pushed
        # down to the single-state DB filter — we fetch unfiltered by state and
        # narrow client-side below.
        problematic_mode = status_choice == PROBLEMATIC_FILTER_LABEL
        if status_choice == "All" or problematic_mode:
            filter_state = None
        else:
            filter_state = status_choice
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
        
        # Narrow to the problematic scan states when that shortcut is selected.
        if problematic_mode:
            files = [f for f in files if f.get("scan_state") in PROBLEMATIC_STATES]
        
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
        
        # Honor the Hide/Show Skipped toggle on the freshly-built rows.
        self._apply_skipped_visibility()
        
        # Update status counts
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
        full_reason = file_dict.get("stderr_tail") or ""
        reason = full_reason[:60]
        reason_item = QTableWidgetItem(reason)
        # Tooltip: full reason plus a triage explanation when recognized, so
        # the user can see at a glance whether a re-download is likely to help.
        tooltip = full_reason
        triage = scanner.triage_corruption(full_reason)
        if triage:
            fixhint = (
                "Re-download will LIKELY fix this."
                if triage["fixable"]
                else "Re-download may NOT help (source likely bad)."
            )
            tooltip = (
                f"{full_reason}\n\n{triage['label']}: {triage['explanation']}\n\n{fixhint}"
            )
        if tooltip:
            reason_item.setToolTip(tooltip)
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
        
        # Grayed-out + italic when a delete + re-download has been requested:
        # the original listing has been removed / is being replaced by Radarr.
        if remed in REMEDIATED_REMEDIATION_STATES:
            self._style_row_remediated(row)
    
    def _style_row_remediated(self, row: int):
        """Gray out and italicize every text cell in a row to show the original
        file has been deleted and a fresh download requested from Radarr."""
        muted = QColor(REMEDIATED_ROW_COLOR)
        for col in range(self._table.columnCount()):
            item = self._table.item(row, col)
            if item is None:
                continue
            font = item.font()
            font.setItalic(True)
            # Drop any bold emphasis (e.g. CORRUPT) now that it's remediated.
            font.setBold(False)
            item.setFont(font)
            item.setForeground(muted)
    
    @Slot()
    def _toggle_show_skipped(self):
        """Show or hide rows whose Remediation is SKIPPED. Immediate."""
        self._show_skipped = not self._show_skipped
        self._skip_toggle_btn.setText(
            "Show Skipped" if not self._show_skipped else "Hide Skipped"
        )
        self._apply_skipped_visibility()

    def _apply_skipped_visibility(self):
        """Hide/show table rows based on the Hide/Show Skipped toggle.

        'Skipped' means the file's Remediation state is SKIPPED — the ones you
        marked 'Mark as Skipped' to leave alone. This is a concrete, stable
        property, so the toggle works the same whether or not a scan is running.
        """
        hide = not getattr(self, "_show_skipped", True)
        for row in range(self._table.rowCount()):
            remed_item = self._table.item(row, COL_REMEDIATION)
            remed = remed_item.text() if remed_item else ""
            self._table.setRowHidden(row, hide and remed == "SKIPPED")

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
            self._timeout_combo.setEnabled(False)
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
            self._timeout_combo.setEnabled(True)
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
        # Switch to live mode if not already, then fall through to start the scan
        if self._view_mode == "database":
            self._view_mode_combo.setCurrentText("Live Scan (Start Fresh)")
        
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
        timeout_sec = _TIMEOUT_MAP.get(self._timeout_combo.currentText(), 1800)
        
        # Force live mode NOW (setCurrentText above dispatches asynchronously, so
        # self._view_mode may still read "database" here). Setting it directly
        # ensures the live-update slots (_on_scan_start etc.) fire.
        self._view_mode = "live"
        
        # Reset the per-worker activity panel for the new scan.
        self._worker_rows_clear()
        self._worker_sizes = {}
        
        # Pre-populate the table from the database for the selected libraries so
        # the screen isn't blank while the scan runs. Folders that were recently
        # scanned (and therefore skipped) still show their known status; folders
        # being (re)scanned update in place as results arrive.
        self._preload_live_table(roots)
        
        # Disable scan controls during scan (but leave action buttons enabled)
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._lib_ah.setEnabled(False)
        self._lib_is.setEnabled(False)
        self._lib_tz.setEnabled(False)
        self._workers_combo.setEnabled(False)
        self._timeout_combo.setEnabled(False)
        
        # Reset progress bar to definite mode (no pulsing)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Starting...")
        
        # Start worker (worker will create its own DB connection)
        self._worker = ScanWorker(roots, workers, rescan=False, limit=None, timeout_sec=timeout_sec)
        self._worker.discovery.connect(self._on_discovery)
        self._worker.scan_start.connect(self._on_scan_start)
        self._worker.scan_size_known.connect(self._on_scan_size_known)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.result_row.connect(self._on_result_row)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()
    
    def _preload_live_table(self, roots):
        """Fill the Live table from the DB for the selected libraries up front.

        Without this, a resumed scan (which skips folders scanned < 7 days ago)
        shows an empty table because only freshly-scanned folders emit rows. By
        pre-loading, the user immediately sees the library and watches statuses
        update in place as the scan progresses.
        """
        self._live_scan_paths = set()
        self._table.setRowCount(0)

        if not self._db_conn:
            return

        # Normalize the selected roots to strings for prefix matching.
        root_strs = [str(r) for r in roots]

        try:
            all_files = db.get_files(self._db_conn)
        except Exception:
            all_files = []

        self._table.setSortingEnabled(False)
        count = 0
        for f in all_files:
            fp = f.get("folder_path") or ""
            # Only include folders under one of the selected library roots.
            if not any(fp.startswith(rs) for rs in root_strs):
                continue
            self._live_scan_paths.add(fp)
            self._add_file_row(f)
            count += 1
        self._table.setSortingEnabled(True)

        self._update_status_counts()
        if count:
            self._info_label.setText(
                f"🔴 Live scan - {count} known files preloaded; statuses update as the scan runs"
            )
        # Respect the current Hide/Show Skipped state on the freshly-built table.
        self._apply_skipped_visibility()

    @Slot()
    def _stop_scan(self):
        """Stop the current scan."""
        if not self._worker:
            return
        
        # Disconnect signals FIRST to prevent late signal processing during shutdown
        try:
            self._worker.discovery.disconnect()
            self._worker.scan_start.disconnect()
            self._worker.scan_size_known.disconnect()
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
        
        # Reset any folders still stuck in SCANNING state (worker was killed
        # before it could write their results) back to UNKNOWN so they get
        # picked up on the next scan.
        try:
            table = db._files_table(self._db_conn)
            ph = db._ph(self._db_conn)
            db._execute(
                self._db_conn,
                f"UPDATE {table} SET scan_state = 'UNKNOWN', worker_id = NULL, lock_until = NULL "
                f"WHERE scan_state = {ph}",
                ("SCANNING",),
            )
        except Exception:
            pass
        
        # Reset UI - stop the pulsating animation by setting a definite range
        self._progress_label.setText("Scan stopped")
        self._progress_bar.setRange(0, 100)  # Definite range stops pulsing
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Stopped")
        self._stop_btn.setEnabled(False)
        self._worker = None
        
        # Clear the per-worker activity panel.
        self._worker_rows_clear()
        
        # Re-enable scan controls based on current view mode
        if self._view_mode == "live":
            self._scan_btn.setEnabled(True)
            self._lib_ah.setEnabled(True)
            self._lib_is.setEnabled(True)
            self._lib_tz.setEnabled(True)
            self._workers_combo.setEnabled(True)
            self._timeout_combo.setEnabled(True)
    
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
        # Add a per-worker progress line (regardless of view mode) so all
        # concurrently-scanning files are visible at the top of the app.
        self._worker_row_add(folder_path)
        
        # Only add table row in live mode
        if self._view_mode != "live":
            return
        
        # Track this path as part of current scan session
        if not hasattr(self, '_live_scan_paths'):
            self._live_scan_paths = set()
        self._live_scan_paths.add(folder_path)
        
        # If the folder was pre-loaded, just flip its existing row to SCANNING
        # rather than adding a duplicate.
        normalized = str(Path(folder_path))
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_FOLDER)
            if item and item.data(Qt.ItemDataRole.UserRole):
                if str(Path(item.data(Qt.ItemDataRole.UserRole))) == normalized:
                    self._set_row_status(row, "SCANNING")
                    reason_item = self._table.item(row, COL_REASON)
                    if reason_item:
                        reason_item.setText("Scanning...")
                    return
        
        # Otherwise add a fresh placeholder row
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
    
    @Slot(str, "qint64")
    def _on_scan_size_known(self, folder_path: str, size_bytes: int):
        """Update size cell as soon as file is found, before null_decode starts."""
        # Remember the size for this file's per-worker progress line (shown in
        # any view mode).
        if not hasattr(self, "_worker_sizes"):
            self._worker_sizes = {}
        self._worker_sizes[folder_path] = size_bytes
        self._worker_row_update(folder_path, 0.0, size_bytes)
        
        if self._view_mode != "live":
            return
        normalized_path = str(Path(folder_path))
        for row in range(self._table.rowCount()):
            folder_item = self._table.item(row, COL_FOLDER)
            if folder_item:
                row_path = folder_item.data(Qt.ItemDataRole.UserRole)
                if row_path and str(Path(row_path)) == normalized_path:
                    size_item = self._table.item(row, COL_SIZE)
                    if size_item:
                        size_item.setText(_size_display(size_bytes))
                        size_item.setData(Qt.ItemDataRole.UserRole + 1, size_bytes)
                    break
    
    @Slot(str, float)
    def _on_file_progress(self, folder_path: str, elapsed_sec: float):
        """Handle per-file progress update during scan."""
        # Ignore late signals when no worker
        if self._worker is None:
            return
        
        minutes = int(elapsed_sec // 60)
        seconds = int(elapsed_sec % 60)
        
        # Update this file's own per-worker progress line.
        self._worker_row_update(folder_path, elapsed_sec,
                                self._worker_sizes.get(folder_path) if hasattr(self, "_worker_sizes") else None)
        
        # Keep a compact summary on the shared label (count of files in flight).
        active = len(self._worker_rows)
        self._progress_label.setText(
            f"⏱ Scanning {active} file(s)…" if active else "Scanning…"
        )
        
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
        
        # This file finished — drop its per-worker progress line.
        self._worker_row_remove(folder_path)
        if hasattr(self, "_worker_sizes"):
            self._worker_sizes.pop(folder_path, None)
        
        active = len(self._worker_rows)
        summary = f"⏱ Scanning {active} file(s)…" if active else f"{status}: {folder_name}"
        self._progress_label.setText(summary)
        # Keep the bar's maximum in sync with the actual work total (folders to
        # scan this run), so the percentage is meaningful.
        if total > 0 and self._progress_bar.maximum() != total:
            self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_bar.setFormat(f"{current}/{total} ({100*current//total if total > 0 else 0}%)")
        
        # In live mode, update just the existing row in place (don't refresh whole table)
        if self._view_mode == "live":
            self._update_row_state(folder_path, state)
            self._update_status_counts()
    
    # ------------------------------------------------------------------
    #  Per-worker activity panel
    # ------------------------------------------------------------------
    def _worker_row_add(self, folder_path: str):
        """Add (or reuse) a per-file progress row: name + pulsing bar + timer.

        A normal scan has no percentage (ffmpeg just decodes end-to-end), so the
        bar is indeterminate/pulsing to show the file is actively being worked.
        """
        if folder_path in self._worker_rows:
            return
        name = Path(folder_path).name

        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        name_lbl = QLabel(f"⏱ {name}")
        name_lbl.setObjectName("statusLabel")
        name_lbl.setMinimumWidth(280)
        row.addWidget(name_lbl)

        bar = QProgressBar()
        bar.setRange(0, 0)          # 0/0 => indeterminate (pulsing)
        bar.setTextVisible(False)
        bar.setFixedHeight(14)
        row.addWidget(bar, 1)

        timer_lbl = QLabel("0m 00s")
        timer_lbl.setObjectName("statusLabel")
        timer_lbl.setMinimumWidth(70)
        row.addWidget(timer_lbl)

        self._worker_rows[folder_path] = {
            "container": container, "name": name_lbl,
            "bar": bar, "timer": timer_lbl, "size": None,
        }
        self._worker_panel.addWidget(container)
        container.show()

    def _worker_row_update(self, folder_path: str, elapsed_sec: float, size_bytes: int = None):
        """Update the timer (and optional size) on a folder's progress row."""
        rec = self._worker_rows.get(folder_path)
        if rec is None:
            self._worker_row_add(folder_path)
            rec = self._worker_rows.get(folder_path)
            if rec is None:
                return
        if size_bytes:
            rec["size"] = size_bytes
        name = Path(folder_path).name
        size_txt = f"  [{_size_display(rec['size'])}]" if rec.get("size") else ""
        rec["name"].setText(f"⏱ {name}{size_txt}")
        minutes = int(elapsed_sec // 60)
        seconds = int(elapsed_sec % 60)
        rec["timer"].setText(f"{minutes}m {seconds:02d}s")

    def _worker_row_remove(self, folder_path: str):
        """Remove a folder's progress row once it finishes."""
        rec = self._worker_rows.pop(folder_path, None)
        if rec is not None:
            self._worker_panel.removeWidget(rec["container"])
            rec["container"].deleteLater()

    def _worker_rows_clear(self):
        """Remove all per-file progress rows (scan start/stop/finish)."""
        for rec in list(self._worker_rows.values()):
            self._worker_panel.removeWidget(rec["container"])
            rec["container"].deleteLater()
        self._worker_rows.clear()

    def _set_header_tooltip(self, col: int, text: str):
        """Attach a tooltip to a horizontal header section."""
        item = self._table.horizontalHeaderItem(col)
        if item is not None:
            item.setToolTip(text)

    def _set_row_status(self, row: int, state: str):
        """Set the Status (verdict) cell text + color for a given row."""
        verdict_item = self._table.item(row, COL_VERDICT)
        if not verdict_item:
            return
        verdict_item.setText(state)
        if state in STATE_COLORS:
            verdict_item.setForeground(QColor(STATE_COLORS[state]))

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
        self._timeout_combo.setEnabled(True)
        
        # Clean up worker
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        
        # Clear the per-worker activity panel.
        self._worker_rows_clear()
        
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
                # If the stored reason carries a triage label, append the full
                # explanation so the user understands what the error means and
                # whether a re-download is likely to help.
                triage = scanner.triage_corruption(log)
                if triage:
                    fixhint = (
                        "A fresh re-download will LIKELY fix this."
                        if triage["fixable"]
                        else "A re-download may NOT help — the source release is likely bad."
                    )
                    log = (
                        f"{log}\n\n"
                        f"── Diagnosis ──\n"
                        f"Type: {triage['label']}\n"
                        f"{triage['explanation']}\n\n"
                        f"{fixhint}\n\n"
                        f"Tip: use 'Deep Inspect (ffprobe)' to confirm whether only "
                        f"the end of the file is broken (truncated download) versus "
                        f"whole-file corruption."
                    )
                self._show_text_dialog(f"ffmpeg Log - {Path(path).name}", log)

    def _show_text_dialog(self, title: str, text: str, actions: list = None):
        """Show a resizable, scrollable, monospace text dialog.

        actions: optional list of (label, callback, is_primary) tuples rendered
        as buttons to the left of Close. Each callback runs after the dialog is
        accepted/closed, so it can safely open its own dialogs.
        """
        from PySide6.QtWidgets import (
            QDialog, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QPushButton
        )
        from PySide6.QtGui import QFont

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(760, 560)
        layout = QVBoxLayout(dlg)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        editor.setFont(mono)
        layout.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        pending = {"cb": None}

        for entry in (actions or []):
            label, cb = entry[0], entry[1]
            is_primary = len(entry) > 2 and entry[2]
            b = QPushButton(label)
            if is_primary:
                b.setObjectName("primary")

            def _make_handler(callback):
                def _handler():
                    pending["cb"] = callback
                    dlg.accept()
                return _handler

            b.clicked.connect(_make_handler(cb))
            btn_row.addWidget(b)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        dlg.exec()

        # Run the chosen action after the dialog has closed.
        if pending["cb"]:
            pending["cb"]()

    @Slot()
    def _deep_inspect(self, folder_path: str):
        """Run ffprobe/ffmpeg deep inspection on the folder's video file.

        Diagnoses whether a CORRUPT file is truly unrecoverable (source/container
        damage) or has a fixable problem (e.g. a truncated download). Runs in a
        background thread and shows the report in a scrollable dialog.
        """
        from app.workers import InspectWorker

        # Resolve the actual video file: prefer the DB's stored video_path,
        # else fall back to the largest video in the folder.
        video_path = None
        files = db.get_files(self._db_conn)
        record = next((f for f in files if f["folder_path"] == folder_path), None)
        if record:
            video_path = record.get("video_path")

        if not video_path or not Path(video_path).exists():
            found = scanner.largest_video_in_folder(Path(folder_path))
            video_path = str(found) if found else None

        if not video_path:
            QMessageBox.warning(
                self,
                "No Video File",
                f"Could not find a video file to inspect in:\n{Path(folder_path).name}",
            )
            return

        # Guard against launching two inspections at once.
        if getattr(self, "_inspect_worker", None) and self._inspect_worker.isRunning():
            QMessageBox.information(
                self, "Inspection Running", "A deep inspection is already in progress."
            )
            return

        folder_name = Path(folder_path).name
        self._progress_label.setText(f"🔬 Inspecting: {folder_name} (running ffprobe...)")

        # Show a determinate progress dialog. The inspection has three cheap
        # phases (ffprobe -> header -> tail); the worker emits a fraction at
        # each boundary so the bar advances meaningfully with no extra cost.
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import Qt as _Qt

        busy = QProgressDialog(
            f"Inspecting {folder_name}...\nRunning ffprobe + header/tail decode.",
            "Cancel", 0, 100, self
        )
        busy.setWindowTitle("Deep Inspect")
        busy.setWindowModality(_Qt.WindowModality.WindowModal)
        busy.setAutoClose(False)
        busy.setAutoReset(False)
        busy.setMinimumDuration(0)
        busy.setValue(0)
        self._inspect_dialog = busy

        def on_inspect_progress(frac, label):
            if getattr(self, "_inspect_dialog", None) is None:
                return
            pct = int(max(0.0, min(1.0, frac)) * 100)
            busy.setValue(pct)
            busy.setLabelText(f"Inspecting {folder_name}...\n{label} ({pct}%)")

        self._inspect_video_path = video_path  # remember for a possible full decode
        self._inspect_folder_path = folder_path  # remember for a possible remediation
        self._inspect_worker = InspectWorker(video_path)
        self._inspect_worker.progress.connect(on_inspect_progress)
        self._inspect_worker.finished.connect(
            lambda result: self._on_inspect_finished(folder_path, folder_name, result)
        )
        self._inspect_worker.error.connect(self._on_inspect_error)
        busy.canceled.connect(self._inspect_worker.cancel)
        self._inspect_worker.start()

    def _close_inspect_dialog(self):
        """Close and dispose the inspect busy dialog if present."""
        if getattr(self, "_inspect_dialog", None):
            self._inspect_dialog.reset()
            self._inspect_dialog.deleteLater()
            self._inspect_dialog = None

    @Slot(dict)
    def _on_inspect_finished(self, folder_path: str, folder_name: str, result: dict):
        """Show the deep-inspection report. Offer a one-click Delete + Re-search
        when the diagnosis says a re-download will fix it; offer a full deep
        decode when the quick check was inconclusive."""
        self._close_inspect_dialog()
        self._progress_label.setText(f"Inspection complete: {folder_name}")
        report = result.get("report") or result.get("summary") or "No output."

        if getattr(self, "_inspect_worker", None):
            self._inspect_worker.deleteLater()
            self._inspect_worker = None

        # Build contextual action buttons for the report dialog.
        actions = []
        if result.get("fixable") is True:
            actions.append((
                "Delete + Re-search (Radarr)",
                lambda: self._remediate_paths([folder_path]),
                True,  # primary
            ))
        elif result.get("ambiguous"):
            actions.append((
                "Run Full Deep Decode",
                lambda: self._full_decode(
                    folder_path,
                    folder_name,
                    getattr(self, "_inspect_video_path", None),
                    result.get("duration_sec"),
                ),
                False,
            ))

        self._show_text_dialog(f"Deep Inspect - {folder_name}", report, actions=actions)

    def _full_decode(self, folder_path: str, folder_name: str, video_path: str, duration_sec):
        """Run the full-file deep decode in the background with a progress dialog."""
        from app.workers import FullDecodeWorker
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import Qt as _Qt

        if not video_path:
            QMessageBox.warning(self, "No Video File", "Could not resolve the video file to decode.")
            return

        if getattr(self, "_fulldecode_worker", None) and self._fulldecode_worker.isRunning():
            QMessageBox.information(self, "Decode Running", "A full decode is already in progress.")
            return

        # Modal progress dialog with a Cancel button.
        dlg = QProgressDialog(
            f"Deep decoding {folder_name}...", "Cancel", 0, 100, self
        )
        dlg.setWindowTitle("Full Deep Decode")
        dlg.setWindowModality(_Qt.WindowModality.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        self._fulldecode_dialog = dlg

        worker = FullDecodeWorker(video_path, duration_sec=duration_sec)
        self._fulldecode_worker = worker

        def on_progress(frac, elapsed):
            if frac is None:
                # Unknown total — show elapsed time and pulse.
                dlg.setRange(0, 0)
                dlg.setLabelText(f"Deep decoding {folder_name}...\nElapsed: {int(elapsed)}s")
            else:
                dlg.setRange(0, 100)
                dlg.setValue(int(frac * 100))
                dlg.setLabelText(
                    f"Deep decoding {folder_name}...\n"
                    f"{int(frac * 100)}%  (elapsed {int(elapsed)}s)"
                )

        worker.progress.connect(on_progress)
        worker.finished.connect(
            lambda result: self._on_full_decode_finished(folder_path, folder_name, result)
        )
        worker.error.connect(self._on_full_decode_error)
        dlg.canceled.connect(worker.cancel)

        self._progress_label.setText(f"🔬 Full decode: {folder_name} (this may take a while)...")
        worker.start()

    @Slot(dict)
    def _on_full_decode_finished(self, folder_path: str, folder_name: str, result: dict):
        """Show the full-decode error map + verdict, and offer a one-click
        Delete + Re-search when the verdict says a re-download will fix it."""
        if getattr(self, "_fulldecode_dialog", None):
            self._fulldecode_dialog.reset()
            self._fulldecode_dialog.deleteLater()
            self._fulldecode_dialog = None

        verdict = result.get("verdict", "")
        self._progress_label.setText(f"Full decode complete: {folder_name} — {verdict}")
        report = result.get("report") or "No output."

        actions = []
        if result.get("fixable") is True:
            actions.append((
                "Delete + Re-search (Radarr)",
                lambda: self._remediate_paths([folder_path]),
                True,
            ))

        self._show_text_dialog(f"Full Deep Decode - {folder_name}", report, actions=actions)

        if getattr(self, "_fulldecode_worker", None):
            self._fulldecode_worker.deleteLater()
            self._fulldecode_worker = None

    @Slot(str)
    def _on_full_decode_error(self, msg: str):
        if getattr(self, "_fulldecode_dialog", None):
            self._fulldecode_dialog.reset()
            self._fulldecode_dialog.deleteLater()
            self._fulldecode_dialog = None
        self._progress_label.setText("Full decode failed")
        QMessageBox.critical(self, "Full Decode Error", f"Full decode failed:\n{msg}")
        if getattr(self, "_fulldecode_worker", None):
            self._fulldecode_worker.deleteLater()
            self._fulldecode_worker = None

    @Slot(str)
    def _on_inspect_error(self, msg: str):
        """Handle a deep-inspection failure."""
        self._close_inspect_dialog()
        self._progress_label.setText("Inspection failed")
        QMessageBox.critical(self, "Inspection Error", f"Deep inspect failed:\n{msg}")
        if getattr(self, "_inspect_worker", None):
            self._inspect_worker.deleteLater()
            self._inspect_worker = None
    
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
        
        inspect_action = menu.addAction("🔬 Deep Inspect (ffprobe)")
        inspect_action.triggered.connect(lambda: self._deep_inspect(path))
        
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
        """Execute remediation on all QUEUED files."""
        queued = db.get_files(self._db_conn, filter_remediation="QUEUED")
        if not queued:
            QMessageBox.warning(self, "No Files Queued", "No files are queued for remediation")
            return
        folder_paths = [f["folder_path"] for f in queued]
        self._remediate_paths(folder_paths)

    def _remediate_paths(self, folder_paths: list):
        """Confirm, then delete + Radarr re-search the given folder path(s).

        Shared by the "Delete + Re-search" button (whole QUEUED set) and by the
        one-click action offered from a deep-inspect diagnosis.
        """
        from radarr import RadarrClient
        from app.workers import RemediateWorker

        if not folder_paths:
            return

        # Don't launch a second remediation on top of a running one.
        if getattr(self, "_remediate_worker", None) and self._remediate_worker.isRunning():
            QMessageBox.information(
                self, "Remediation Running", "A remediation is already in progress."
            )
            return

        if len(folder_paths) == 1:
            detail = f"'{Path(folder_paths[0]).name}'"
        else:
            detail = f"{len(folder_paths)} file(s)"
        reply = QMessageBox.question(
            self,
            "Confirm Remediation",
            f"This will:\n"
            f"1. Delete {detail} from disk\n"
            f"2. Tell Radarr to re-search for a fresh download\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            radarr = RadarrClient()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to connect to Radarr: {e}")
            return

        # Make sure these are marked QUEUED so the table/state stays consistent.
        db.mark_queued(self._db_conn, folder_paths)
        self._refresh_table()

        self._remediate_worker = RemediateWorker(
            folder_paths=folder_paths,
            radarr_client=radarr,
            dry_run=False,
            max_batch=None,
        )
        self._remediate_worker.step.connect(self._on_remediate_step)
        self._remediate_worker.finished.connect(self._on_remediate_finished)
        self._remediate_worker.error.connect(self._on_error)

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
        
        # Stop any running deep-inspection worker
        if getattr(self, "_inspect_worker", None) and self._inspect_worker.isRunning():
            self._inspect_worker.cancel()
            self._inspect_worker.wait(2000)
            self._kill_ffmpeg_processes()
        self._close_inspect_dialog()

        # Stop any running full-decode worker
        if getattr(self, "_fulldecode_worker", None) and self._fulldecode_worker.isRunning():
            self._fulldecode_worker.cancel()
            self._fulldecode_worker.wait(2000)
            self._kill_ffmpeg_processes()

        # Close database connection
        if self._db_conn:
            self._db_conn.close()
        
        event.accept()
