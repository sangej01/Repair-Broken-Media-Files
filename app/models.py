"""Data models for GUI state."""
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class FileRecord:
    """Represents a single file record from the database."""
    id: int
    folder_path: str
    video_path: Optional[str]
    size_bytes: int
    duration_sec: Optional[float]
    scan_state: str  # UNKNOWN, CLEAN, CORRUPT, ERROR, EMPTY
    last_scan_at: Optional[str]
    last_scan_secs: Optional[float]
    stderr_tail: Optional[str]
    radarr_movie_id: Optional[int]
    radarr_tmdb_id: Optional[int]
    remediation: str  # NONE, QUEUED, DELETING, DELETED, RESEARCHING, REMEDIATED, FAILED, SKIPPED
    remediation_at: Optional[str]
    remediation_log: Optional[str]
    attempts: int
    first_seen_at: str
    notes: Optional[str]


@dataclass
class ScanProgress:
    """Progress update during scan."""
    current: int
    total: int
    folder_name: str
    state: str


@dataclass
class ScanStats:
    """Summary statistics after scan completes."""
    folders_total: int
    folders_done: int
    clean_count: int
    corrupt_count: int
    error_count: int
    empty_count: int
    elapsed_sec: float


@dataclass
class RemediationAction:
    """Single remediation step."""
    folder_path: str
    action: str  # 'delete', 'radarr_unmonitor', 'radarr_search', etc.
    status: str  # 'pending', 'running', 'success', 'failed'
    message: str
