# Repair Broken Media Files

Scan your movie library for structurally corrupted files and remediate them automatically via Radarr.

![Version](https://img.shields.io/badge/version-1.0-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Overview

**Repair Broken Media Files** detects the "File ended prematurely" corruption pattern (and 14+ other signatures) that affected movies like *28 Years Later* and *Naked (1993)*. It automates the remediation workflow: delete corrupt files and trigger Radarr to re-acquire clean copies.

### Key Features

- ✅ **Deep Corruption Scan** - ffmpeg null-decode detects mid-file corruption that `ffprobe` misses
- ✅ **Live Progress** - See movies appear instantly with real-time scanning timers
- ✅ **Interactive GUI** - Queue, inspect, and remediate while scanning continues
- ✅ **CLI Support** - Automate scans via command line
- ✅ **SQLite Tracking** - Resumable scans, full audit trail
- ✅ **Radarr Integration** - Automated delete + re-search workflow
- ✅ **Pluck Integration** - VERIFY_LEVEL=3 prevents re-acquiring bad files
- ✅ **Attempts Tracking** - Visual warnings for movies that fail repeatedly (systemic issue detection)
- ✅ **Adaptive Timeout** - 2 min/GB minimum (handles large 4K files automatically)
- ✅ **Multiple Scan States** - CLEAN/CORRUPT/ERROR/TIMEOUT/MISSING/EMPTY for accurate categorization

---

## Quick Start

### Starting the App

```powershell
cd "C:\Users\sangej01\Desktop\Python Scripts\Media Tools Consortium\Repair Broken Media Files"
pipenv run python main.py
```

### Stopping & Quitting

| Action | Method |
|--------|--------|
| **Stop scan** | Click "Stop" button or press **Esc** |
| **Quit app** | Press **Ctrl+Q** or **Ctrl+W** or close window |
| **Force quit** | If frozen: Task Manager → End Task |

✅ Safe to quit anytime - database commits per-file  
✅ Resume on restart - scans are resumable  

### Keyboard Shortcuts

- **Ctrl+Q** / **Ctrl+W** - Quit
- **Esc** - Stop scan
- **Ctrl+R** - Refresh table
- **Ctrl+F** - Search

---

### Installation

```powershell
cd "Repair Broken Media Files"
pipenv install
```

### Configure

Copy `.env.example` to `.env`:

```bash
RADARR_URL=http://mforum-ms01-a:8989
RADARR_API=your-api-key-here
```

### Launch GUI

```powershell
pipenv run python main.py
```

### Or Use CLI

```powershell
# Scan library
pipenv run python main.py scan --limit 10

# Show corrupt files
pipenv run python main.py list --corrupt

# Queue all corrupt files
pipenv run python main.py queue --all-corrupt

# Preview remediation
pipenv run python main.py remediate --dry-run
```

---

## How It Works

### 1. Scan

```
Select libraries → Set workers → Start Scan
         ↓
Movies appear with "SCANNING" state
         ↓
Live timer: "⏱ Scanning: Movie Name (2m 15s)"
         ↓
Results: CLEAN | CORRUPT | ERROR | TIMEOUT | MISSING | EMPTY
```

### 2. Queue

```
Right-click CORRUPT movie → Queue for Remediation
         ↓
State: NONE → QUEUED
```

### 3. Remediate

```
Click "Delete + Re-search" → Confirm
         ↓
Delete file from disk
         ↓
Radarr: unmonitor → delete record → monitor → search
         ↓
State: QUEUED → DELETED → RESEARCHING
```

### 4. Verify

```
Radarr downloads new copy
         ↓
Pluck Movies (VERIFY_LEVEL=3) verifies
         ↓
If CLEAN: imports to library
         ↓
Re-scan → State: REMEDIATED
```

---

## Screenshots

### Main Window
```
┌──────────────────────────────────────────────────────────────────┐
│  Repair Broken Media Files                                       │
├──────────────────────────────────────────────────────────────────┤
│  Library: ☑ A-H  ☑ I-S  ☑ T-Z   Parallel scans: [2▾]            │
│  ⏱ Scanning: 28 Years Later (2m 15s)       [████░░] 45/100      │
├──────────────────────────────────────────────────────────────────┤
│  ☑ │ 28 Years Later (2025) │ 11.7G│ CORRUPT │ File ended prema…│
│  ☐ │ Naked (1993)          │  7.3G│ CORRUPT │ File ended prema…│
│  ☐ │ Ryans Daughter (1970) │  2.8G│ CLEAN   │                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Documentation

- **[USERGUIDE.md](docs/USERGUIDE.md)** - Complete usage guide (step-by-step workflows, CLI commands, troubleshooting)
- **[WORKFLOW.md](docs/WORKFLOW.md)** - Task-oriented walkthrough with examples
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)** - Build standalone .exe and deploy to other PCs
- **[FUTURE.md](docs/FUTURE.md)** - Roadmap and future enhancements (multi-PC, PostgreSQL, etc.)
- **[DISASTER_RECOVERY.md](docs/DISASTER_RECOVERY.md)** - How to restore from GitHub if local PC fails

---

## Requirements

- Python 3.11+
- ffmpeg (on PATH or at `C:\ffmpeg\bin\ffmpeg.exe`)
- Radarr instance
- Movie library access (Z:\Movies or similar)

---

## Performance

| Library Size | Workers | Scan Time     |
|--------------|---------|---------------|
| ~500 movies  | 2       | 6-12 hours    |
| ~1000 movies | 4       | 8-16 hours    |
| ~3600 movies | 4       | 24-48 hours   |

**Per-file:** 30s - 6min depending on size and corruption location.

---

## Integration

### Pluck Movies
Set `VERIFY_LEVEL = 3` in `config/movies.py` to catch corruption before import.

### Movie-Library-Compressor
Reads library roots from `compressor.yaml` automatically.

### Radarr Import
Shares `.env` configuration (RADARR_URL, RADARR_API).

---

## Corruption Patterns Detected

- File ended prematurely
- Non-monotonically increasing DTS
- decode_slice errors
- Missing reference frames
- Invalid NAL unit size
- Truncated packets
- Corrupt EBML headers
- ... and 7 more (see `scanner.py:TROUBLE_KEYWORDS`)

---

## CLI Examples

```powershell
# Scan A-H library with 4 workers
pipenv run python main.py scan --root "Z:\Movies\A-H" --workers 4

# Show corrupt files sorted by size
pipenv run python main.py list --corrupt --sort size_bytes

# Queue specific movie
pipenv run python main.py queue "28 Years Later"

# Remediate max 5 files (dry run)
pipenv run python main.py remediate --dry-run --max 5

# Remediate all queued files
pipenv run python main.py remediate
```

---

## Database

SQLite database at `repair.db`:
- **files** table - scan results, remediation state
- **runs** table - audit trail

Resumable scans (skips folders scanned < 7 days ago unless `--rescan`).

---

## State Machine

### Scan States
`UNKNOWN → [CLEAN | CORRUPT | ERROR | TIMEOUT | MISSING | EMPTY]`

| State | Color | Meaning |
|-------|-------|---------|
| 🟢 CLEAN | Green | File is fine |
| 🔴 CORRUPT | Red (bold) | Has structural corruption |
| 🟡 ERROR | Yellow | ffmpeg couldn't process (real error) |
| 🟠 TIMEOUT | Orange | Scan timed out (file too large/NAS slow) |
| 🟣 MISSING | Purple | Folder no longer exists on disk |
| ⚪ EMPTY | Grey | No video file found |

### Remediation States
`NONE → QUEUED → DELETED → RESEARCHING → [REMEDIATED | FAILED]`

---

## Troubleshooting

### ffmpeg not found
```powershell
winget install ffmpeg
```

### Movie not found in Radarr
Ensure folder name matches: `Movie Title (YYYY)`

### Scan is slow
- Increase workers (4-8 for 1 GbE NAS)
- Check network speed
- Normal: 30s-6min per file

---

## Version History

**v1.0** (2026-06-17)
- Initial release
- GUI + CLI
- Full scan & remediation workflow
- Radarr integration

---

## License

MIT License - Part of the Media Tools Consortium

---

## Support

See [USERGUIDE.md](docs/USERGUIDE.md) for detailed help.

Report issues at your GitHub repository.

---

*Built to solve the 28 Years Later / Naked corruption issue caused by SAB/par2 silent failures on Whatbox.*
