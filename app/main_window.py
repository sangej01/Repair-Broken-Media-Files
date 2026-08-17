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
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

# Corruption triage classes (a sub-classification of CORRUPT files only):
#   A  = re-download friendly  (triage fixable is True)  — truncated, missing
#        frames, concealed, no-frame, generic corruption. A fresh copy usually
#        fixes it → recommended action is Delete + Re-search.
#   B  = likely source damage  (triage fixable is False) — broken container,
#        encoder artifact, malformed packet, timestamp problems. A re-download
#        of the SAME release probably won't help → recommended action is a
#        Deep Inspect before deciding.
#   U  = unclassified          (triage_corruption() returned None) — CORRUPT but
#        the reason matched no known signature. Never auto-acted on.
CLASS_A = "A"
CLASS_B = "B"
CLASS_UNCLASSIFIED = "U"

# Filter-combo labels → class code (None = "All types", no class narrowing).
CORRUPT_CLASS_LABELS = {
    "All types": None,
    "A (re-download)": CLASS_A,
    "B (source damage)": CLASS_B,
    "Unclassified": CLASS_UNCLASSIFIED,
}

# Remediation states meaning the original file has been deleted and a fresh
# download requested from Radarr. Used by the batch-target logic to skip rows
# that are already being handled (do NOT add SKIPPED here — that would change
# batch targeting semantics).
REMEDIATED_REMEDIATION_STATES = {"DELETED", "RESEARCHING", "REMEDIATED"}
# Remediation states that are "handled" and should render grayed-out + italic in
# the table: the active-remediation states above plus SKIPPED (user chose to
# leave it alone). Purely cosmetic — controls row styling only.
DIMMED_REMEDIATION_STATES = REMEDIATED_REMEDIATION_STATES | {"SKIPPED"}
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


def corruption_class(file_dict: dict):
    """Return the triage class (CLASS_A / CLASS_B / CLASS_UNCLASSIFIED) for a
    CORRUPT file, or None for non-CORRUPT rows.

    The class is derived on the fly from the stored ffmpeg reason via
    scanner.triage_corruption() — there is no persisted triage column. A reason
    that matches no known signature is CLASS_UNCLASSIFIED (never auto-acted on).
    """
    if (file_dict.get("scan_state") or "") != "CORRUPT":
        return None
    triage = scanner.triage_corruption(file_dict.get("stderr_tail") or "")
    if triage is None:
        return CLASS_UNCLASSIFIED
    return CLASS_A if triage.get("fixable") else CLASS_B


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
        self._lib_ah.toggled.connect(self._update_coverage)
        scan_row.addWidget(self._lib_ah)
        
        self._lib_is = QCheckBox("I-S")
        self._lib_is.setChecked(True)
        self._lib_is.toggled.connect(self._update_coverage)
        scan_row.addWidget(self._lib_is)
        
        self._lib_tz = QCheckBox("T-Z")
        self._lib_tz.setChecked(True)
        self._lib_tz.toggled.connect(self._update_coverage)
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
        
        # --- Overall library coverage row (persists across sessions) ---
        coverage_row = QHBoxLayout()
        coverage_row.setSpacing(8)
        cov_lbl = QLabel("Library:")
        cov_lbl.setObjectName("statusLabel")
        coverage_row.addWidget(cov_lbl)
        self._coverage_bar = QProgressBar()
        self._coverage_bar.setRange(0, 100)
        self._coverage_bar.setValue(0)
        self._coverage_bar.setTextVisible(True)
        self._coverage_bar.setFixedHeight(18)
        self._coverage_bar.setToolTip(
            "Overall scan coverage of your whole library (folders with a result "
            "vs. folders on disk). Persists across sessions — this is the big "
            "picture, not just the current run."
        )
        coverage_row.addWidget(self._coverage_bar, 1)
        layout.addLayout(coverage_row)
        
        # --- Session progress row (current run) ---
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
        _corrupt_class_tip = (
            "Filter CORRUPT files by triage class:\n"
            "  A (re-download) — truncated/missing frames; a fresh download will fix it.\n"
            "  B (source damage) — broken container or encoder artifact from the source;\n"
            "    re-downloading the same release won't help.\n"
            "  Unclassified — CORRUPT but no rule matched the error signature.\n\n"
            "Select A or B to enable the context batch button at the bottom."
        )
        _corrupt_class_lbl = QLabel("Corruption type:")
        _corrupt_class_lbl.setToolTip(_corrupt_class_tip)
        filter_row.addWidget(_corrupt_class_lbl)
        self._corrupt_class_combo = QComboBox()
        self._corrupt_class_combo.setToolTip(_corrupt_class_tip)
        self._corrupt_class_combo.addItems(list(CORRUPT_CLASS_LABELS.keys()))
        self._corrupt_class_combo.setFixedWidth(150)
        self._corrupt_class_combo.currentTextChanged.connect(self._apply_filter)
        self._corrupt_class_combo.currentTextChanged.connect(
            lambda _: self._update_batch_class_button()
        )
        filter_row.addWidget(self._corrupt_class_combo)

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
        
        # --- Scan Activity log (persistent, append-only feed) ---
        self._activity_box = QGroupBox("Scan Activity")
        self._activity_box.setCheckable(True)
        self._activity_box.setChecked(True)  # expanded by default
        self._activity_box.setToolTip(
            "Every scan result as it happens, accumulated across runs (persisted "
            "to the database). Uncheck the title to collapse."
        )
        act_layout = QVBoxLayout(self._activity_box)
        act_layout.setContentsMargins(8, 4, 8, 8)
        act_layout.setSpacing(4)

        act_controls = QHBoxLayout()
        self._activity_problems_only = QCheckBox("Only problems")
        self._activity_problems_only.setToolTip(
            "Show only non-CLEAN results (CORRUPT / TIMEOUT / ERROR / etc.)."
        )
        self._activity_problems_only.toggled.connect(self._reload_activity_log)
        act_controls.addWidget(self._activity_problems_only)
        act_controls.addStretch()
        self._activity_count = QLabel("")
        self._activity_count.setObjectName("statusLabel")
        act_controls.addWidget(self._activity_count)
        clear_btn = QPushButton("Clear Log")
        clear_btn.setToolTip("Delete all recorded scan-activity events (does not touch scan results).")
        clear_btn.clicked.connect(self._clear_activity_log)
        act_controls.addWidget(clear_btn)
        act_layout.addLayout(act_controls)

        self._activity_list = QListWidget()
        self._activity_list.setMaximumHeight(160)
        self._activity_list.setAlternatingRowColors(True)
        act_layout.addWidget(self._activity_list)

        # Collapse/expand: hide the inner widgets when unchecked.
        self._activity_box.toggled.connect(
            lambda on: [self._activity_list.setVisible(on),
                        self._activity_problems_only.setVisible(on),
                        self._activity_count.setVisible(on),
                        clear_btn.setVisible(on)]
        )
        layout.addWidget(self._activity_box)
        
        # --- Bottom status bar ---
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        
        self._status_label = QLabel("Ready")
        self._status_label.setObjectName("summary")
        status_row.addWidget(self._status_label)
        
        layout.addLayout(status_row)
        
        # --- Action buttons: 2-row grid panel ---
        # Row 0 (utilities + scan + housekeeping):
        #   Select All | Select None  |sep|  Re-scan TIMEOUTs | Check Re-downloads | Backup DB  |sep|  Open Folder | Show ffmpeg Log
        # Row 1 (remediation — full width):
        #   Queue for Remediation | Delete + Re-search | Re-search all Group A/B
        btn_panel = QWidget()
        btn_grid = QGridLayout(btn_panel)
        btn_grid.setSpacing(6)
        btn_grid.setContentsMargins(0, 4, 0, 4)

        # --- Row 0: utility buttons ---
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._select_all)
        btn_grid.addWidget(self._select_all_btn, 0, 0)

        self._select_none_btn = QPushButton("Select None")
        self._select_none_btn.clicked.connect(self._select_none)
        btn_grid.addWidget(self._select_none_btn, 0, 1)

        def _vsep():
            f = QFrame()
            f.setFrameShape(QFrame.Shape.VLine)
            f.setFrameShadow(QFrame.Shadow.Sunken)
            return f

        btn_grid.addWidget(_vsep(), 0, 2)

        self._rescan_timeouts_btn = QPushButton("Re-scan TIMEOUTs")
        self._rescan_timeouts_btn.setToolTip(
            "Re-scan every file currently in TIMEOUT state. Most TIMEOUTs are "
            "transient NAS I/O stalls and come back CLEAN on a fresh scan."
        )
        self._rescan_timeouts_btn.clicked.connect(self._rescan_timeouts)
        btn_grid.addWidget(self._rescan_timeouts_btn, 0, 3)

        self._check_redl_btn = QPushButton("Check Re-downloads")
        self._check_redl_btn.setToolTip(
            "Ask Radarr which RESEARCHING movies have finished re-downloading "
            "(imported), which are still downloading, and which are pending — "
            "so you know what's ready to re-scan without opening Radarr."
        )
        self._check_redl_btn.clicked.connect(self._check_redownloads)
        btn_grid.addWidget(self._check_redl_btn, 0, 4)

        self._backup_btn = QPushButton("Backup DB")
        self._backup_btn.setToolTip(
            "Save a timestamped snapshot of the database to the backup folder "
            "(also happens automatically on exit)."
        )
        self._backup_btn.clicked.connect(self._backup_db_now)
        btn_grid.addWidget(self._backup_btn, 0, 5)

        btn_grid.addWidget(_vsep(), 0, 6)

        self._open_folder_btn = QPushButton("Open Folder")
        self._open_folder_btn.clicked.connect(self._open_folder)
        btn_grid.addWidget(self._open_folder_btn, 0, 7)

        self._show_log_btn = QPushButton("Show ffmpeg Log")
        self._show_log_btn.clicked.connect(self._show_log)
        btn_grid.addWidget(self._show_log_btn, 0, 8)

        # Spacer column pushes utility buttons left, remediation buttons span full width
        btn_grid.setColumnStretch(9, 1)

        # --- Row 1: remediation buttons (span all columns) ---
        self._queue_btn = QPushButton("Queue for Remediation")
        self._queue_btn.clicked.connect(self._queue_selected)
        btn_grid.addWidget(self._queue_btn, 1, 0, 1, 3)

        self._remediate_btn = QPushButton("Delete + Re-search")
        self._remediate_btn.setObjectName("danger")
        self._remediate_btn.setToolTip(
            "Delete the movie file(s) and ask Radarr for a fresh download.\n\n"
            "Acts on the CHECKED rows if any are ticked. If nothing is ticked, "
            "it falls back to every file already in the QUEUED state.\n"
            "Either way, it lists exactly what will be deleted and asks you to "
            "confirm first."
        )
        self._remediate_btn.clicked.connect(self._remediate_queued)
        btn_grid.addWidget(self._remediate_btn, 1, 3, 1, 3)

        self._batch_class_btn = QPushButton("Re-search all Group A")
        self._batch_class_btn.setToolTip(
            "Context-sensitive batch action for the selected Corruption type:\n"
            "  A → Delete + Re-search all Group A targets (confirm first).\n"
            "  B → Deep Inspect all Group B targets sequentially, then show a summary.\n"
            "Select 'A (re-download)' or 'B (source damage)' in the Corruption type\n"
            "filter to enable this button."
        )
        self._batch_class_btn.setEnabled(False)
        self._batch_class_btn.clicked.connect(self._batch_class_action)
        btn_grid.addWidget(self._batch_class_btn, 1, 6, 1, 4)

        layout.addWidget(btn_panel)
    
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
        # Load the persisted scan-activity feed (answers "what happened last run").
        self._reload_activity_log()
        # Show overall library coverage for the selected libraries.
        self._update_coverage()
    
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
        # Corruption-class filter is client-side (class is computed on the fly
        # from stderr_tail; no DB column). Selecting a class implies CORRUPT, so
        # an AND-narrow on top of whatever Status yields is intentional — if the
        # Status combo already excludes CORRUPT the result is simply empty.
        selected_class = CORRUPT_CLASS_LABELS.get(self._corrupt_class_combo.currentText())
        
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

        # Narrow by corruption class when the combo is set to A, B, or Unclassified.
        if selected_class is not None:
            files = [f for f in files if corruption_class(f) == selected_class]

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

        # Sync batch-class button label/enabled with current filter selection.
        self._update_batch_class_button()
    
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
    
    def _selected_roots(self):
        """Return (roots, label) for the currently-checked libraries.

        Falls back to all libraries when none are checked (e.g. Database view).
        """
        default_roots = config.get_library_roots()
        picks = []
        labels = []
        checks = [
            (self._lib_ah, "A-H", 0),
            (self._lib_is, "I-S", 1),
            (self._lib_tz, "T-Z", 2),
        ]
        for cb, name, idx in checks:
            if cb.isChecked() and len(default_roots) > idx:
                picks.append(default_roots[idx])
                labels.append(name)
        if not picks:
            picks = list(default_roots)
            labels = ["A-H", "I-S", "T-Z"][:len(default_roots)]
        return picks, "+".join(labels) if labels else "Library"

    def _update_coverage(self):
        """Update the overall library-coverage bar for the SELECTED libraries.

        Denominator = folders on disk under the checked roots. Numerator =
        folders under those roots that already have a definitive verdict
        (CLEAN/CORRUPT/EMPTY/MISSING). Directory counting is cached so it isn't
        re-walked on every folder completion.
        """
        if not self._db_conn:
            return
        roots, label = self._selected_roots()
        root_strs = [str(r) for r in roots]

        # Cache the on-disk folder count per root-set (walking dirs is cheap but
        # not free; recompute only when the selection changes).
        cache_key = "|".join(sorted(root_strs))
        if getattr(self, "_coverage_cache_key", None) != cache_key:
            total = 0
            for r in roots:
                try:
                    total += sum(1 for p in Path(r).iterdir() if p.is_dir())
                except Exception:
                    pass
            self._coverage_cache_key = cache_key
            self._coverage_total = total
        total = getattr(self, "_coverage_total", 0)

        definitive = {"CLEAN", "CORRUPT", "EMPTY", "MISSING"}
        scanned = 0
        try:
            for f in db.get_files(self._db_conn):
                fp = f.get("folder_path") or ""
                if f.get("scan_state") in definitive and any(fp.startswith(rs) for rs in root_strs):
                    scanned += 1
        except Exception:
            pass

        if total > 0:
            pct = int(min(100, scanned * 100 / total))
            remaining = max(0, total - scanned)
            self._coverage_bar.setRange(0, 100)
            self._coverage_bar.setValue(pct)
            self._coverage_bar.setFormat(
                f"{label}: {scanned} / {total} scanned ({pct}%) · {remaining} left"
            )
        else:
            self._coverage_bar.setRange(0, 100)
            self._coverage_bar.setValue(0)
            self._coverage_bar.setFormat(f"{label}: no folders found")

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
        
        # Grayed-out + italic once the row is "handled": a delete + re-download
        # has been requested (DELETED/RESEARCHING/REMEDIATED), or the user chose
        # to leave it alone (SKIPPED). Signals the disposition is decided.
        if remed in DIMMED_REMEDIATION_STATES:
            self._style_row_remediated(row)
    
    def _style_row_remediated(self, row: int):
        """Gray out and italicize every text cell in a row whose disposition is
        handled — either being replaced by Radarr (DELETED/RESEARCHING/
        REMEDIATED) or deliberately left alone (SKIPPED)."""
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

    # ------------------------------------------------------------------ #
    #  Corruption-class filter helpers (Tasks 4-8)                        #
    # ------------------------------------------------------------------ #

    def _class_targets(self, class_code: str) -> list:
        """Return folder paths for the batch-class action.

        Checked visible rows are used if any are checked (intersected with
        class_code so stray non-class checks are silently ignored). When
        nothing is checked, fall back to ALL currently-visible rows of
        that class that are not already in an active remediation state
        (DELETED / RESEARCHING / REMEDIATED are skipped — they're already
        being handled and re-queuing them would be wrong).
        """
        # Build a quick lookup: folder_path -> file_dict for all DB rows so
        # corruption_class() has stderr_tail available.
        all_files = db.get_files(self._db_conn) if self._db_conn else []
        file_by_path = {f["folder_path"]: f for f in all_files}

        def _is_class(folder_path: str) -> bool:
            return corruption_class(file_by_path.get(folder_path, {})) == class_code

        # Checked visible rows (if any) — checked rows are explicit intent so
        # we don't filter out active-remediation states here; the confirm dialog
        # will make it obvious if a row is already RESEARCHING.
        checked = self._checked_folder_paths()
        checked_class = [p for p in checked if _is_class(p)]
        if checked_class:
            return checked_class

        # Fallback: all visible (non-hidden) rows of this class that are not
        # already in an active remediation state.
        targets = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            item = self._table.item(row, COL_FOLDER)
            if item is None:
                continue
            folder_path = item.data(Qt.ItemDataRole.UserRole)
            if not folder_path or not _is_class(folder_path):
                continue
            remed_item = self._table.item(row, COL_REMEDIATION)
            remed = remed_item.text() if remed_item else ""
            if remed in REMEDIATED_REMEDIATION_STATES:
                continue
            targets.append(folder_path)
        return targets

    def _update_batch_class_button(self):
        """Sync label and enabled-state of _batch_class_btn with the current
        Corruption type combo selection.  Called from filter change and
        _refresh_table so the button always reflects what's on screen.
        """
        if not hasattr(self, "_batch_class_btn"):
            return  # UI not built yet
        selected_class = CORRUPT_CLASS_LABELS.get(self._corrupt_class_combo.currentText())
        if selected_class == CLASS_A:
            self._batch_class_btn.setText("Re-search all Group A")
            self._batch_class_btn.setToolTip(
                "Delete + Re-search every visible Group A (re-download friendly) "
                "CORRUPT file.\nChecked rows take priority over all visible rows."
            )
            self._batch_class_btn.setEnabled(True)
        elif selected_class == CLASS_B:
            self._batch_class_btn.setText("Inspect all Group B")
            self._batch_class_btn.setToolTip(
                "Run Deep Inspect sequentially on every visible Group B (source "
                "damage) CORRUPT file, then show a grouped summary.\n"
                "Checked rows take priority over all visible rows."
            )
            self._batch_class_btn.setEnabled(True)
        else:
            self._batch_class_btn.setText("Re-search all Group A")
            self._batch_class_btn.setToolTip(
                "Select 'A (re-download)' or 'B (source damage)' in the "
                "Corruption type filter to enable this button."
            )
            self._batch_class_btn.setEnabled(False)

    @Slot()
    def _batch_class_action(self):
        """Dispatch the context batch action for the selected corruption class."""
        selected_class = CORRUPT_CLASS_LABELS.get(self._corrupt_class_combo.currentText())
        if selected_class == CLASS_A:
            paths = self._class_targets(CLASS_A)
            if not paths:
                QMessageBox.information(
                    self, "No Targets",
                    "No Group A (re-download friendly) files found in the current view."
                )
                return
            self._remediate_paths(paths, source="class-a")
        elif selected_class == CLASS_B:
            paths = self._class_targets(CLASS_B)
            if not paths:
                QMessageBox.information(
                    self, "No Targets",
                    "No Group B (source damage) files found in the current view."
                )
                return
            self._start_batch_inspect(paths)

    # ------------------------------------------------------------------ #
    #  Class B — sequential batch Deep Inspect (Task 7)                   #
    # ------------------------------------------------------------------ #

    def _start_batch_inspect(self, folder_paths: list):
        """Start a sequential Deep Inspect pass over *folder_paths*.

        Runs one InspectWorker at a time to stay within the single-worker
        contract.  A QProgressDialog lets the user cancel mid-batch.
        """
        from app.workers import InspectWorker
        from PySide6.QtWidgets import QProgressDialog
        from PySide6.QtCore import Qt as _Qt

        # Guard: don't start if a single right-click inspect is running.
        if getattr(self, "_inspect_worker", None) and self._inspect_worker.isRunning():
            QMessageBox.information(
                self, "Inspection Running",
                "A deep inspection is already in progress. "
                "Wait for it to finish or cancel it first."
            )
            return
        # Guard: don't nest batch inspections.
        if getattr(self, "_batch_inspect_running", False):
            QMessageBox.information(
                self, "Batch Inspection Running",
                "A batch inspection is already in progress."
            )
            return

        self._batch_inspect_running = True
        self._batch_inspect_queue = list(folder_paths)
        self._batch_inspect_results = []   # list of (folder_name, diagnosis, fixable, error)
        self._batch_inspect_total = len(folder_paths)

        self._batch_inspect_dialog = QProgressDialog(
            "Starting batch inspection…", "Cancel",
            0, self._batch_inspect_total, self
        )
        self._batch_inspect_dialog.setWindowTitle("Inspect all Group B")
        self._batch_inspect_dialog.setWindowModality(_Qt.WindowModality.WindowModal)
        self._batch_inspect_dialog.setAutoClose(False)
        self._batch_inspect_dialog.setAutoReset(False)
        self._batch_inspect_dialog.setMinimumDuration(0)
        self._batch_inspect_dialog.setValue(0)
        self._batch_inspect_dialog.canceled.connect(self._cancel_batch_inspect)

        self._batch_class_btn.setEnabled(False)
        self._batch_inspect_next()

    def _batch_inspect_next(self):
        """Pop the next folder from the queue and start an InspectWorker for it."""
        from app.workers import InspectWorker

        done = self._batch_inspect_total - len(self._batch_inspect_queue)

        # Queue exhausted or dialog was closed (cancelled).
        if not self._batch_inspect_queue or not getattr(self, "_batch_inspect_running", False):
            self._finish_batch_inspect()
            return

        folder_path = self._batch_inspect_queue.pop(0)
        folder_name = Path(folder_path).name

        # Update progress dialog.
        dlg = getattr(self, "_batch_inspect_dialog", None)
        if dlg:
            dlg.setValue(done)
            dlg.setLabelText(
                f"Inspecting {done + 1} / {self._batch_inspect_total}:\n{folder_name}"
            )

        # Resolve video path (same logic as _deep_inspect).
        video_path = None
        if self._db_conn:
            all_files = db.get_files(self._db_conn)
            record = next((f for f in all_files if f["folder_path"] == folder_path), None)
            if record:
                video_path = record.get("video_path")
        if not video_path or not Path(video_path).exists():
            found = scanner.largest_video_in_folder(Path(folder_path))
            video_path = str(found) if found else None

        if not video_path:
            self._batch_inspect_results.append(
                (folder_name, "Could not locate a video file", None, True)
            )
            self._batch_inspect_next()
            return

        # Start the worker; on finished/error, advance to the next item.
        worker = InspectWorker(video_path)
        self._inspect_worker = worker  # reuse the single-inspect guard slot

        def _on_done(result):
            diag = result.get("diagnosis") or result.get("summary") or "No diagnosis"
            fixable = result.get("fixable")
            self._batch_inspect_results.append((folder_name, diag, fixable, False))
            self._inspect_worker = None
            self._batch_inspect_next()

        def _on_err(msg):
            self._batch_inspect_results.append((folder_name, f"Error: {msg}", None, True))
            self._inspect_worker = None
            self._batch_inspect_next()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        worker.start()

    def _cancel_batch_inspect(self):
        """Cancel the running batch inspection (called when user hits Cancel)."""
        self._batch_inspect_running = False
        self._batch_inspect_queue = []
        worker = getattr(self, "_inspect_worker", None)
        if worker and worker.isRunning():
            worker.cancel()

    def _finish_batch_inspect(self):
        """Called when the batch queue is empty (or cancelled).

        For DEFINITIVE results, act immediately:
          - fixable=True  → Delete + Re-search (same release is fine).
          - fixable=False → Delete + Blocklist + search for a DIFFERENT release.
        Only inconclusive and error results go to the summary dialog.
        """
        self._batch_inspect_running = False

        dlg = getattr(self, "_batch_inspect_dialog", None)
        if dlg:
            dlg.reset()
            dlg.deleteLater()
            self._batch_inspect_dialog = None

        self._update_batch_class_button()  # re-enable if appropriate

        results = getattr(self, "_batch_inspect_results", [])
        if not results:
            return

        # Group results.
        fixable_entries = []   # fixable is True  → re-download (same release ok)
        bad_source = []        # fixable is False → blocklist + different release
        inconclusive = []      # fixable is None, no error → needs human decision
        errors = []            # inspect failed

        for (name, diag, fixable, is_error) in results:
            if is_error:
                errors.append((name, diag))
            elif fixable is True:
                fixable_entries.append((name, diag))
            elif fixable is False:
                bad_source.append((name, diag))
            else:
                inconclusive.append((name, diag))

        # Build folder-path lookup for all DB rows (by folder name).
        all_files = db.get_files(self._db_conn) if self._db_conn else []
        file_by_name = {Path(f["folder_path"]).name: f["folder_path"] for f in all_files}

        def _paths_for(entries):
            paths = []
            for name, _ in entries:
                p = file_by_name.get(name)
                if p:
                    paths.append(p)
            return paths

        # --- Act on definitive results immediately ---

        fixable_paths = _paths_for(fixable_entries)
        bad_source_paths = _paths_for(bad_source)

        # Fire bad-source blocklist remediation first.  If the user confirms and
        # a worker starts, we can't immediately fire the fixable remediation on
        # top of it — the concurrent-run guard would silently block it.  Instead,
        # stash the fixable paths and let _on_remediate_finished pick them up once
        # the bad-source worker completes.
        self._pending_fixable_paths = []
        if bad_source_paths:
            self._remediate_paths(bad_source_paths, source="class-b-badsource", blocklist=True)

        worker_running = (
            getattr(self, "_remediate_worker", None)
            and self._remediate_worker.isRunning()
        )
        if fixable_paths:
            if worker_running:
                # Bad-source worker is live — defer fixable paths until it finishes.
                self._pending_fixable_paths = fixable_paths
            else:
                # Bad-source was skipped (user said No) or there were none — fire now.
                self._remediate_paths(fixable_paths, source="class-b-fixable", blocklist=False)

        # --- Show summary only for things that need a human decision ---
        undecided = inconclusive + errors
        if not undecided and not bad_source_paths and not fixable_paths:
            return  # nothing to show

        lines = [f"Batch Deep Inspect — {len(results)} file(s) inspected\n"]

        def _section(header, entries):
            if not entries:
                return
            lines.append(f"\n{'─' * 50}")
            lines.append(f"{header} ({len(entries)})")
            for name, diag in entries:
                lines.append(f"  • {name}")
                lines.append(f"      {diag}")

        if fixable_entries:
            _section(
                "Truncated / re-downloadable [fixable] — remediation queued above",
                fixable_entries,
            )
        if bad_source:
            _section(
                "Bad source — blocklist + different-release search queued above",
                bad_source,
            )
        if inconclusive:
            _section("Inconclusive — needs manual review", inconclusive)
        if errors:
            _section("Errors — inspect could not run", errors)

        summary_text = "\n".join(lines)
        self._show_text_dialog("Batch Deep Inspect — Group B Summary", summary_text)

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
        
        # Acquire the cross-process scan lock so a CLI scan can't collide with us
        # (two SQLite writers can hang/crash the app).
        import scanlock
        if scanlock.acquire("gui") is None:
            QMessageBox.warning(
                self, "Scanner Busy",
                f"Cannot start: {scanlock.holder_description()} is already using "
                f"the database.\n\nClose the other scanner and try again.",
            )
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
        
        # Disable scan controls during scan (but leave most action buttons enabled)
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._lib_ah.setEnabled(False)
        self._lib_is.setEnabled(False)
        self._lib_tz.setEnabled(False)
        self._workers_combo.setEnabled(False)
        self._timeout_combo.setEnabled(False)
        # Class-B batch inspect is destructive-adjacent; disable during scan.
        self._batch_class_btn.setEnabled(False)
        
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

    def _checked_folder_paths(self) -> list:
        """Return folder paths for rows that are BOTH displayed AND checked.

        Hidden rows (filtered out / Hide Skipped) are never included, so an
        action can only ever touch movies the user can actually see and has
        ticked.
        """
        paths = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            widget = self._table.cellWidget(row, COL_SELECT)
            if not widget:
                continue
            checkbox = widget.findChild(QCheckBox)
            if checkbox and checkbox.isChecked():
                item = self._table.item(row, COL_FOLDER)
                if item and item.data(Qt.ItemDataRole.UserRole):
                    paths.append(item.data(Qt.ItemDataRole.UserRole))
        return paths

    def _rescan_folders(self, folders: list, label: str = "selected"):
        """Re-scan a specific set of folders (force, ignoring skip rules).

        Safe within the running app: it uses the same worker/lock as a normal
        scan, so it never collides with the GUI's own DB access. Use this for
        TIMEOUTs and other rows worth re-checking — no CLI needed.
        """
        if not folders:
            QMessageBox.information(self, "Nothing to Re-scan",
                                    "No folders to re-scan.")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Scan Running",
                                    "A scan is already running. Stop it first.")
            return

        import scanlock
        if scanlock.acquire("gui") is None:
            QMessageBox.warning(
                self, "Scanner Busy",
                f"Cannot start: {scanlock.holder_description()} is already using "
                f"the database.\n\nClose the other scanner and try again.",
            )
            return

        reply = QMessageBox.question(
            self, "Re-scan",
            f"Re-scan {len(folders)} {label} folder(s) now?\n\n"
            f"This forces a fresh decode (ignores the 'unchanged' skip).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            scanlock.release()
            return

        # Switch to live view so results update in place.
        self._view_mode = "live"
        from pathlib import Path as _P
        folder_paths = [_P(f) for f in folders]

        workers = int(self._workers_combo.currentText())
        timeout_sec = _TIMEOUT_MAP.get(self._timeout_combo.currentText(), 1800)

        self._worker_rows_clear()
        self._worker_sizes = {}
        # Preload just these rows so they're visible and update live.
        self._live_scan_paths = set(str(p) for p in folder_paths)
        self._table.setRowCount(0)
        for fp in folder_paths:
            rec = next((f for f in db.get_files(self._db_conn)
                        if str(_P(f["folder_path"])) == str(fp)), None)
            if rec:
                self._add_file_row(rec)

        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Starting re-scan...")
        self._progress_label.setText(f"Re-scanning {len(folder_paths)} {label} folder(s)...")

        self._worker = ScanWorker([], workers, rescan=True, limit=None,
                                  timeout_sec=timeout_sec, folders=folder_paths)
        self._worker.discovery.connect(self._on_discovery)
        self._worker.scan_start.connect(self._on_scan_start)
        self._worker.scan_size_known.connect(self._on_scan_size_known)
        self._worker.progress.connect(self._on_progress)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.result_row.connect(self._on_result_row)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @Slot()
    def _backup_db_now(self):
        """Manually back up the SQLite DB to the configured backup folder."""
        import dbbackup
        r = dbbackup.backup_db()
        if r.get("ok"):
            extra = f"\nPruned {r['pruned']} old backup(s)." if r.get("pruned") else ""
            QMessageBox.information(
                self, "Backup Complete",
                f"Database backed up to:\n{r['path']}{extra}",
            )
        else:
            QMessageBox.warning(
                self, "Backup Not Done",
                f"Could not back up the database:\n{r.get('error', 'unknown error')}",
            )

    @Slot()
    def _check_redownloads(self):
        """Ask Radarr about the status of all RESEARCHING re-downloads."""
        from app.workers import RadarrStatusWorker
        researching = [r["folder_path"] for r in db.get_files(self._db_conn)
                       if r.get("remediation") == "RESEARCHING"]
        if not researching:
            QMessageBox.information(self, "No Re-downloads",
                                    "No movies are currently in RESEARCHING state.")
            return
        if getattr(self, "_radarr_status_worker", None) and self._radarr_status_worker.isRunning():
            QMessageBox.information(self, "Checking", "A Radarr status check is already running.")
            return

        self._progress_label.setText(f"Checking Radarr for {len(researching)} re-download(s)...")
        self._check_redl_btn.setEnabled(False)
        self._radarr_status_worker = RadarrStatusWorker(researching)
        self._radarr_status_worker.finished.connect(self._on_redownload_status)
        self._radarr_status_worker.error.connect(self._on_redownload_error)
        self._radarr_status_worker.start()

    @Slot(dict)
    def _on_redownload_status(self, result: dict):
        self._check_redl_btn.setEnabled(True)
        self._progress_label.setText("Radarr status check complete")
        # Each bucket is a list of (name, folder_path) tuples.
        imported = result.get("imported", [])
        downloading = result.get("downloading", [])
        pending = result.get("pending", [])
        not_in = result.get("not_in_radarr", [])

        def names(bucket):
            return [b[0] if isinstance(b, (list, tuple)) else b for b in bucket]

        lines = []
        lines.append(f"✓ Imported (ready to re-scan): {len(imported)}")
        for n in names(imported):
            lines.append(f"    {n}")
        lines.append("")
        lines.append(f"⬇ Downloading now: {len(downloading)}")
        for n in names(downloading):
            lines.append(f"    {n}")
        lines.append("")
        lines.append(f"… Pending (searching / nothing grabbed yet): {len(pending)}")
        for n in names(pending):
            lines.append(f"    {n}")
        if not_in:
            lines.append("")
            lines.append(f"⚠ Not found in Radarr (manual handling): {len(not_in)}")
            for n in names(not_in):
                lines.append(f"    {n}")
        lines.append("")
        if imported:
            lines.append("Click 'Re-scan Imported' below to verify the fresh copies "
                         "decode cleanly (Radarr 'imported' only means a file arrived, "
                         "not that it's good).")

        # Offer a one-click action to re-scan exactly the imported movies, so you
        # don't have to go back to the table and select them manually.
        actions = []
        imported_paths = [b[1] for b in imported if isinstance(b, (list, tuple)) and len(b) > 1]
        if imported_paths:
            actions.append((
                f"Re-scan Imported ({len(imported_paths)})",
                lambda: self._rescan_folders(imported_paths, label="imported"),
                True,  # primary button
            ))

        self._show_text_dialog("Radarr Re-download Status", "\n".join(lines), actions=actions)

        if getattr(self, "_radarr_status_worker", None):
            self._radarr_status_worker.deleteLater()
            self._radarr_status_worker = None

    @Slot(str)
    def _on_redownload_error(self, msg: str):
        self._check_redl_btn.setEnabled(True)
        self._progress_label.setText("Radarr status check failed")
        QMessageBox.warning(self, "Radarr Error",
                            f"Could not check Radarr status:\n{msg}")
        if getattr(self, "_radarr_status_worker", None):
            self._radarr_status_worker.deleteLater()
            self._radarr_status_worker = None

    @Slot()
    def _rescan_timeouts(self):
        """Re-scan every folder currently in TIMEOUT state."""
        rows = db.get_files(self._db_conn, filter_state="TIMEOUT")
        folders = [r["folder_path"] for r in rows]
        if not folders:
            QMessageBox.information(self, "No TIMEOUTs",
                                    "There are no TIMEOUT files to re-scan.")
            return
        self._rescan_folders(folders, label="TIMEOUT")

    @Slot()
    def _rescan_selected(self):
        """Re-scan the checked rows (or the current row if none checked)."""
        folders = self._checked_folder_paths()
        if not folders:
            # fall back to the row under the context menu / current selection
            row = self._table.currentRow()
            if row >= 0:
                item = self._table.item(row, COL_FOLDER)
                if item and item.data(Qt.ItemDataRole.UserRole):
                    folders = [item.data(Qt.ItemDataRole.UserRole)]
        self._rescan_folders(folders, label="selected")

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
        
        # Release the cross-process scan lock.
        try:
            import scanlock; scanlock.release()
        except Exception:
            pass
        
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
        """Kill ONLY the ffmpeg processes this app started.

        Uses the scanner's tracked-PID registry, so it targets our own
        null-decode / inspect / full-decode ffmpeg and never touches unrelated
        ffmpeg (e.g. the Movie Library Compressor's encodes running in parallel).
        """
        try:
            scanner._kill_all_active_processes()
        except Exception:
            # Non-critical; the OS reaps our children when the process exits.
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
    
    @Slot(str, float, object)
    def _on_file_progress(self, folder_path: str, elapsed_sec: float, fraction=None):
        """Handle per-file progress update during scan."""
        # Ignore late signals when no worker
        if self._worker is None:
            return
        
        minutes = int(elapsed_sec // 60)
        seconds = int(elapsed_sec % 60)
        
        # Update this file's own per-worker progress line (with % when known).
        self._worker_row_update(
            folder_path, elapsed_sec,
            self._worker_sizes.get(folder_path) if hasattr(self, "_worker_sizes") else None,
            fraction,
        )
        
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
        
        # Append to the live Scan Activity feed (persisted by the scanner; here
        # we just reflect it in the UI immediately).
        self._append_activity_event({"at": None, "folder_path": folder_path, "scan_state": state})
        self._update_activity_count()

        # Advance the overall library-coverage bar as each folder gets a verdict.
        self._update_coverage()

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

    def _worker_row_update(self, folder_path: str, elapsed_sec: float,
                           size_bytes: int = None, fraction=None):
        """Update a folder's progress row: name, size, %-bar (or pulse) + timer.

        `fraction` (0..1) turns the bar determinate with a percentage. When it's
        None (duration unknown) the bar stays indeterminate/pulsing.
        """
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

        bar = rec["bar"]
        if fraction is None:
            # Unknown total — keep it pulsing.
            if bar.maximum() != 0:
                bar.setRange(0, 0)
            bar.setTextVisible(False)
        else:
            pct = int(max(0.0, min(1.0, fraction)) * 100)
            if bar.maximum() != 100:
                bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFormat(f"{pct}%")
            bar.setTextVisible(True)

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

    # ------------------------------------------------------------------
    #  Scan Activity log
    # ------------------------------------------------------------------
    def _activity_line(self, ev: dict) -> str:
        """Format one activity event as a single display line."""
        at = ev.get("at") or ""
        # Show HH:MM:SS from the ISO timestamp; fall back to now for live events.
        ts = ""
        if isinstance(at, str) and "T" in at:
            ts = at.split("T", 1)[1][:8]
        elif at:
            ts = str(at)[-8:]
        else:
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%H:%M:%S")
        state = ev.get("scan_state", "?")
        name = Path(ev.get("folder_path", "")).name
        icon = {
            "CLEAN": "✓", "CORRUPT": "⚠", "TIMEOUT": "⏱",
            "ERROR": "✗", "EMPTY": "○", "MISSING": "?",
        }.get(state, "•")
        return f"{ts}  {icon} {state:8} {name}"

    def _activity_color(self, state: str) -> QColor:
        return QColor(STATE_COLORS.get(state, "#cdd6f4"))

    def _append_activity_event(self, ev: dict, at_top: bool = True):
        """Add one event line to the activity list (respecting the filter)."""
        if self._activity_problems_only.isChecked() and ev.get("scan_state") == "CLEAN":
            return
        item = QListWidgetItem(self._activity_line(ev))
        item.setForeground(self._activity_color(ev.get("scan_state", "")))
        if at_top:
            self._activity_list.insertItem(0, item)
        else:
            self._activity_list.addItem(item)
        # Cap the on-screen list so it never grows unbounded.
        while self._activity_list.count() > 1000:
            self._activity_list.takeItem(self._activity_list.count() - 1)

    def _reload_activity_log(self):
        """Reload the activity list from the database (newest first)."""
        if not self._db_conn:
            return
        self._activity_list.clear()
        try:
            problems = self._activity_problems_only.isChecked()
            events = db.get_scan_events(self._db_conn, limit=500, problems_only=problems)
        except Exception:
            events = []
        # get_scan_events returns newest-first; add in that order (top = newest).
        for ev in events:
            self._append_activity_event(ev, at_top=False)
        self._update_activity_count()

    def _update_activity_count(self):
        try:
            total = len(db.get_scan_events(self._db_conn, limit=100000))
            probs = len(db.get_scan_events(self._db_conn, limit=100000, problems_only=True))
        except Exception:
            total, probs = 0, 0
        self._activity_count.setText(f"{total} events · {probs} problems")

    def _clear_activity_log(self):
        reply = QMessageBox.question(
            self, "Clear Scan Activity Log",
            "Delete all recorded scan-activity events?\n\n"
            "This only clears the activity feed — your scan results (CLEAN/CORRUPT/…) "
            "are not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            table = db._events_table(self._db_conn)
            db._execute(self._db_conn, f"DELETE FROM {table}")
        except Exception:
            pass
        self._reload_activity_log()

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
        self._update_batch_class_button()
        
        # Clean up worker
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        
        # Release the cross-process scan lock.
        try:
            import scanlock; scanlock.release()
        except Exception:
            pass
        
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
        """Select all VISIBLE rows (skips rows hidden by filters/Hide Skipped)."""
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
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
        """Queue for remediation only rows that are displayed AND checked."""
        selected_paths = self._checked_folder_paths()
        
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
        elif verdict == "CLEAN":
            # The full decode found zero real errors — the CORRUPT flag was a
            # false positive. Offer to write the CLEAN verdict directly so the
            # user doesn't have to re-decode the whole file just to clear it.
            actions.append((
                "Mark CLEAN in database",
                lambda: self._mark_clean(folder_path, folder_name),
                True,
            ))
        elif verdict == "PLAYABLE":
            # A few localized errors, but the file is watchable. It's not worth
            # re-downloading and it isn't truly broken, so the useful action is
            # to stop it showing as an unhandled CORRUPT row. Offer Mark as
            # Skipped (keep the file). Re-downloading anyway is still available
            # via the normal Delete + Re-search button if the user prefers.
            actions.append((
                "Mark as Skipped (keep the file)",
                lambda: self._skip_single(folder_path),
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

    def _mark_clean(self, folder_path: str, folder_name: str):
        """Write a CLEAN verdict for a file the Full Deep Decode cleared.

        Offered from the full-decode report when the verdict is CLEAN (zero
        real errors). Records the clean result directly so the row leaves the
        CORRUPT view without a costly full re-scan.
        """
        if not self._db_conn:
            return
        db.mark_clean(self._db_conn, folder_path)
        self._refresh_table()
        self._progress_label.setText(f"Marked CLEAN: {folder_name}")

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
        
        rescan_action = menu.addAction("🔁 Re-scan (selected / this file)")
        rescan_action.triggered.connect(self._rescan_selected)
        
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
        """Execute remediation on the checked rows, or the QUEUED set.

        Targeting rules (chosen to eliminate the "it deleted a movie I never
        selected" surprise):
          * If any rows are CHECKED, act on exactly those — what you see ticked
            is what gets deleted.
          * If NOTHING is checked, fall back to every file already in the
            QUEUED remediation state (the historical behavior).
        Either way, the confirmation dialog names the movies and states WHERE
        the list came from, so the target is never invisible.
        """
        checked = self._checked_folder_paths()
        if checked:
            self._remediate_paths(checked, source="checked")
            return

        queued = db.get_files(self._db_conn, filter_remediation="QUEUED")
        if not queued:
            QMessageBox.warning(
                self, "Nothing to Remediate",
                "No rows are checked and no files are in the QUEUED state.\n\n"
                "Tick the checkbox on the movies you want to fix (or use "
                "'Queue for Remediation'), then try again.",
            )
            return
        folder_paths = [f["folder_path"] for f in queued]
        self._remediate_paths(folder_paths, source="queued")

    def _remediate_paths(self, folder_paths: list, source: str = "selected", blocklist: bool = False):
        """Confirm, then delete + Radarr re-search the given folder path(s).

        Shared by the "Delete + Re-search" button (checked rows or the QUEUED
        set) and by the one-click action offered from a deep-inspect diagnosis.

        `source` describes where the list came from ("checked", "queued", or
        "selected"), so the confirmation dialog can spell out WHY these movies
        were chosen — the key fix for the "it picked a movie I never selected"
        surprise.

        `blocklist=True` uses the history/failed path: Radarr blocklists the
        bad release and searches for a *different* release.  Use this for
        Group B bad-source files.  `blocklist=False` (default) does the normal
        delete + re-search for the same release.
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

        # Group B safety guard: a plain re-search (blocklist=False) grabs the SAME
        # release, which won't help a bad-source (Group B) file — the same broken
        # release comes back. If the target list contains any Group B files and we
        # weren't already told to blocklist, warn and offer to route them through
        # the blocklist path instead. Skipped for the class-b-* sources (they
        # already know exactly what they are) and when already blocklisting.
        if not blocklist and source not in ("class-b-fixable", "class-b-badsource"):
            all_files = db.get_files(self._db_conn) if self._db_conn else []
            class_by_path = {f["folder_path"]: corruption_class(f) for f in all_files}
            group_b = [p for p in folder_paths if class_by_path.get(p) == CLASS_B]
            if group_b:
                b_names = "\n".join(f"  • {Path(p).name}" for p in group_b[:15])
                if len(group_b) > 15:
                    b_names += f"\n  ... and {len(group_b) - 15} more"
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Group B (source damage) detected")
                box.setText(
                    f"{len(group_b)} of the selected file(s) look like "
                    f"Group B — source damage:\n\n{b_names}\n\n"
                    "Re-downloading the SAME release usually returns the same broken "
                    "file. The recommended fix is to BLOCKLIST the bad release and "
                    "have Radarr search for a DIFFERENT one.\n\n"
                    "How do you want to handle these?"
                )
                blocklist_btn = box.addButton(
                    "Blocklist + different release", QMessageBox.ButtonRole.AcceptRole
                )
                plain_btn = box.addButton(
                    "Re-search same release anyway", QMessageBox.ButtonRole.DestructiveRole
                )
                cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
                box.setDefaultButton(blocklist_btn)
                box.exec()
                clicked = box.clickedButton()
                if clicked is cancel_btn:
                    return
                if clicked is blocklist_btn:
                    # Route the Group B files through the blocklist path. Only ONE
                    # remediation can run at a time, so if there are also non-B
                    # files, defer them to a follow-up pass (fired when the
                    # blocklist worker finishes) rather than colliding with the
                    # concurrent-run guard.
                    remaining = [p for p in folder_paths if class_by_path.get(p) != CLASS_B]
                    self._pending_plain_paths = remaining
                    self._remediate_paths(group_b, source="class-b-badsource", blocklist=True)
                    return
                # else: plain_btn — fall through and re-search the same release for all.

        # Explain where this list came from so the target is never a surprise.
        origin = {
            "checked": "the rows you checked",
            "queued": "files already in the QUEUED state (nothing was checked)",
            "class-a": "Group A (re-download friendly) files",
            "class-b-fixable": "Group B files that deep-inspect confirmed are re-downloadable",
            "class-b-badsource": "Group B files confirmed as bad source (will blocklist + search for different release)",
        }.get(source, "your selection")

        names = [Path(p).name for p in folder_paths]
        # Show the actual movies (cap the list so a huge queue stays readable).
        shown = "\n".join(f"  • {n}" for n in names[:15])
        if len(names) > 15:
            shown += f"\n  ... and {len(names) - 15} more"

        if blocklist:
            action_desc = (
                "1. DELETE the file(s) above from disk\n"
                "2. Blocklist the bad release in Radarr (so it won't be grabbed again)\n"
                "3. Tell Radarr to search for a DIFFERENT release"
            )
        else:
            action_desc = (
                "1. DELETE the file(s) above from disk\n"
                "2. Tell Radarr to re-search for a fresh download"
            )

        reply = QMessageBox.question(
            self,
            "Confirm Remediation",
            f"About to remediate {len(folder_paths)} file(s) from {origin}:\n\n"
            f"{shown}\n\n"
            f"This will:\n"
            f"{action_desc}\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # default to No — this is destructive
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
            blocklist=blocklist,
        )
        self._remediate_worker.step.connect(self._on_remediate_step)
        self._remediate_worker.finished.connect(self._on_remediate_finished)
        self._remediate_worker.error.connect(self._on_error)

        self._remediate_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)

        self._batch_class_btn.setEnabled(False)
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
        self._update_batch_class_button()

        # A previous remediation may have deferred follow-up work because only one
        # remediation can run at a time. Fire the next deferred batch now that the
        # worker is free. Order: fixable Group B (from Inspect all Group B), then
        # plain same-release re-searches (from the Group B safety guard).
        for attr, src, bl in (
            ("_pending_fixable_paths", "class-b-fixable", False),
            ("_pending_plain_paths", "selected", False),
        ):
            pending = getattr(self, attr, [])
            if pending:
                setattr(self, attr, [])
                # Clear the stale worker reference BEFORE calling _remediate_paths so
                # its concurrent-run guard doesn't see a "still running" worker.
                if hasattr(self, "_remediate_worker") and self._remediate_worker:
                    self._remediate_worker.deleteLater()
                    self._remediate_worker = None
                # Refresh the table so the just-finished rows move to RESEARCHING.
                self._refresh_table()
                self._remediate_paths(pending, source=src, blocklist=bl)
                return  # skip the normal completion message — next confirm is showing
        
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

        # Final safety net: always kill any ffmpeg WE started that's still alive,
        # regardless of which code path we came through. Targets only our tracked
        # PIDs, so parallel encodes from other apps are left running.
        self._kill_ffmpeg_processes()

        # Release the cross-process scan lock on exit.
        try:
            import scanlock; scanlock.release()
        except Exception:
            pass

        # Auto-backup the SQLite DB to the deploy share (best-effort, off-machine).
        try:
            import dbbackup
            r = dbbackup.backup_db()
            if r.get("ok"):
                print(f"[backup] DB backed up to {r['path']}", flush=True)
            elif r.get("error"):
                print(f"[backup] skipped: {r['error']}", flush=True)
        except Exception:
            pass

        # Close database connection
        if self._db_conn:
            self._db_conn.close()
        
        event.accept()
