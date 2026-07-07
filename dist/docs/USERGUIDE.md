# Repair Broken Media Files - User Guide

## Overview

**Repair Broken Media Files** is a tool that scans your movie library for structurally corrupted video files and helps you remediate them through automated re-acquisition via Radarr.

### What It Does

1. **Scans** your movie library using ffmpeg null-decode to detect corruption
2. **Detects** the "File ended prematurely" pattern and 14+ other corruption signatures
3. **Tracks** results in a SQLite database for systematic remediation
4. **Queues** corrupt files for automated remediation
5. **Deletes** corrupt files and triggers Radarr to re-acquire them
6. **Integrates** with Pluck Movies (VERIFY_LEVEL=3) to prevent re-acquiring bad files

---

## Installation

### Prerequisites

- Python 3.11+
- ffmpeg (on PATH or at `C:\ffmpeg\bin\ffmpeg.exe`)
- Radarr instance (configured in `.env`)
- Access to your movie library (Z:\Movies or similar)

### Setup

1. Navigate to the tool directory:
   ```powershell
   cd "Repair Broken Media Files"
   ```

2. Copy `.env.example` to `.env` and configure:
   ```bash
   RADARR_URL=http://mforum-ms01-a:8989
   RADARR_API=your-api-key-here
   ```

3. Install dependencies:
   ```powershell
   pipenv install
   ```

4. (Optional) Test CLI:
   ```powershell
   pipenv run python main.py scan --limit 5
   ```

---

## GUI Usage

### Launching

```powershell
cd "Repair Broken Media Files"
pipenv run python main.py
```

### Main Window Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Repair Broken Media Files                                       │
│  Scan your movie library for structurally broken files           │
├──────────────────────────────────────────────────────────────────┤
│  Library: ☑ A-H  ☑ I-S  ☑ T-Z   Parallel scans: [2▾]            │
│  [Start Scan]  [Stop]                                            │
├──────────────────────────────────────────────────────────────────┤
│  Scanning: ⏱ Scanning: Movie Name (2m 15s)                      │
│  Progress: [████████░░░░] 45/100 (45%)                           │
├──────────────────────────────────────────────────────────────────┤
│  Filter: [All▾]  Remediation: [Any▾]  Search: [________]        │
├──────────────────────────────────────────────────────────────────┤
│  ☐ │ Folder              │ Size │ Verdict │ Reason            │  │
│  ☑ │ 28 Years Later      │ 11.7G│ CORRUPT │ File ended prema… │  │
│  ☐ │ Naked (1993)        │  7.3G│ CORRUPT │ File ended prema… │  │
│  ☐ │ Ryans Daughter      │  2.8G│ CLEAN   │                   │  │
├──────────────────────────────────────────────────────────────────┤
│  Status: 100 total, 47 corrupt, 51 clean, 2 error               │
├──────────────────────────────────────────────────────────────────┤
│  [Select All] [Select None]  [Queue] [Delete+Re-search]         │
│  [Open Folder] [Show ffmpeg Log]                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## Starting, Stopping, and Restarting

### Quick Reference

#### Start the App
```powershell
cd "C:\Users\sangej01\Desktop\Python Scripts\Media Tools Consortium\Repair Broken Media Files"
pipenv run python main.py
```

#### Stop a Running Scan
1. Click the **"Stop"** button (turns red during scan)
2. Wait 1-2 seconds for scan to terminate
3. Status shows: "Scan stopped"

#### Quit the App
1. **If scan is running:** Click "Stop" first, then close
2. **Or:** Click X → Confirm "Stop and exit"
3. Window closes safely

#### Restart
1. Close the app (see above)
2. Re-run: `pipenv run python main.py`
3. Previous scan results remain in database
4. Can resume scanning or view results

### View Modes

The app has two view modes accessible via the dropdown at the top:

#### **Database (Show All Results)** 💾
- Shows ALL previously scanned files from database
- Persists across app restarts
- Apply filters to find specific files
- Scan controls are disabled (view-only mode)
- **Use this to:** Review previous scan results, queue files for remediation, inspect corruption

#### **Live Scan (Start Fresh)** 🔴
- Starts with empty table
- Populates ONLY as current scan runs
- Shows real-time progress with timers
- Scan controls are enabled
- **Use this to:** Watch new scans happen, see live progress

**Key Point:** Even in Live Scan mode, all results are saved to the database. Switch back to Database View to see everything.

### Database Persistence

✅ **Safe to quit anytime** - Database commits per-file  
✅ **Resume scanning** - Skips folders scanned < 7 days ago  
✅ **View previous results** - Switch to "Database (Show All Results)" mode  
✅ **Queue persists** - Queued files remain queued after restart  
✅ **Both modes use same database** - Live mode saves, Database mode displays  

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Ctrl+Q** | Quit app |
| **Ctrl+W** | Quit app |
| **Esc** | Stop scan |
| **Ctrl+R** | Refresh table |
| **Ctrl+F** | Focus search box |

---

## Step-by-Step Workflow

### 1. Scanning the Library

1. **Select Libraries:**
   - Check the libraries you want to scan (A-H, I-S, T-Z)
   - Default: All three are selected

2. **Set Parallel Scans:**
   - Click the **Parallel scans** dropdown (next to Library checkboxes)
   - Choose 1-8 (default: 2)
   - Higher number = scans more movies at once = faster but more CPU/disk usage
   - Recommendation: 2-4 for 1 GbE NAS

3. **Click "Start Scan"**
   - Movies appear in the table immediately with "SCANNING" state
   - Live timer shows: "⏱ Scanning: Movie Name (2m 15s)"
   - Progress bar updates in real-time

4. **During Scan:**
   - ✅ **You can interact with completed movies**
   - Right-click any row for quick actions
   - Queue corrupt files as they're found
   - Open folders to inspect files
   - View ffmpeg logs to see corruption details

5. **Stopping:**
   - Click "Stop" to terminate scan
   - Safe to close app (asks for confirmation)

### 2. Reviewing Results

#### Table Columns:
- **☐** - Checkbox for bulk selection
- **Folder** - Movie folder name
- **Size** - File size (sortable numerically)
- **Status** - Scan result (CLEAN/CORRUPT/etc.)
- **Reason** - ffmpeg error details (for non-CLEAN files)
- **Remediation** - Remediation state (NONE/QUEUED/DELETED/etc.)
- **Attempts** - Number of remediation attempts:
  - 0 = Never remediated
  - 1 = Remediated once
  - 2 = ⚠️ Bold orange - second attempt (something's wrong)
  - 3+ = 🔴 Bold red - persistent issue (systematic problem!)

#### Status Colors:
- 🟢 **CLEAN** (green) - File passed integrity check
- 🔴 **CORRUPT** (red, bold) - File has structural corruption
- 🟡 **ERROR** (yellow) - ffmpeg couldn't process file (real error)
- 🟠 **TIMEOUT** (orange) - Scan exceeded timeout - file too large or NAS too slow
- 🟣 **MISSING** (purple) - Folder no longer exists on disk
- ⚪ **EMPTY** (grey) - No video file found in folder
- 🔵 **SCANNING** (blue) - Currently being scanned by a worker. The
  `worker_id` column shows which PC is scanning it. Other PCs running scans
  will see this lock and skip the folder until completion (or until the
  60-minute lock expires).
- ⚫ **UNKNOWN** (default) - Status not yet determined

**About UNKNOWN status:** This is a normal, expected status. Movies become UNKNOWN when:
- Folders are discovered but not yet scanned (e.g., during initial scan)
- A scan is interrupted before reaching them
- A previous failed scan (TIMEOUT/ERROR) was reset for retry

UNKNOWN movies will be processed on the next scan. To convert UNKNOWN movies to a definitive status (CLEAN/CORRUPT/etc.), simply run a scan. The scanner prioritizes UNKNOWN and failed-state movies (no 7-day skip applies).

**Filter to UNKNOWN** to see what hasn't been scanned yet - useful for verifying scan completeness.

#### Filtering:
- **Status dropdown:** Show only CORRUPT, CLEAN, ERROR, TIMEOUT, MISSING, EMPTY, UNKNOWN, or All
- **Remediation dropdown:** Filter by NONE, QUEUED, DELETED, REMEDIATED, SKIPPED
- **Search box:** Type to filter by folder name

#### Why Attempts Matter:
If a movie shows **2+ attempts**, it means the remediation cycle is failing repeatedly:
- Same release being downloaded again? → Indexer needs different release
- New copy still corrupt? → Possible upstream issue (Pluck rsync, indexer source)
- 3+ attempts: **Stop and investigate** - there's a systematic problem

#### Viewing Details:
1. Click on a movie
2. Click **"Show ffmpeg Log"** to see corruption details
3. Example log:
   ```
   File ended prematurely at position 1234567890
   [matroska,webm @ 0x...] File ended prematurely
   ```

### 3. Queuing for Remediation

#### Method 1: Select and Queue (Multiple Files)
1. Check the boxes next to corrupt files
2. Click **"Queue for Remediation"**
3. State changes: NONE → QUEUED

#### Method 2: Right-Click Menu (Single File)
1. Right-click on a corrupt movie
2. Select **"➕ Queue for Remediation"**
3. Instantly queued

#### Method 3: CLI
```powershell
pipenv run python main.py queue --all-corrupt
```

### 4. Remediation (Delete + Re-search)

⚠️ **Warning:** This deletes files from disk and triggers Radarr searches.

1. **Queue files first** (see step 3)

2. **Click "Delete + Re-search"**
   - Confirmation dialog appears
   - Shows how many files will be deleted

3. **Remediation Process:**
   For each queued file:
   - ✓ Look up movie in Radarr
   - ✓ Delete file from disk (`shutil.rmtree`)
   - ✓ Unmonitor in Radarr
   - ✓ Delete moviefile record
   - ✓ Re-monitor
   - ✓ Trigger Radarr search
   - State: QUEUED → DELETED → RESEARCHING

4. **After Radarr Re-acquires:**
   - Pluck Movies (VERIFY_LEVEL=3) will verify the new copy
   - If clean: imports to library
   - If corrupt: rejects and you can remediate again

---

## Right-Click Context Menu

Right-click on any movie row to access (menu items vary by current state):

**Always available:**
- **📁 Open Folder** - Opens folder in Windows Explorer
- **📄 Show ffmpeg Log** - View corruption details / scan output
- **🚫 Mark as Skipped** - Don't remediate this one
- **🔍 Verify Folder Exists** - Check if folder is still on disk
- **📋 Copy Path** - Copy folder path to clipboard

**For CORRUPT files (state=NONE):**
- **➕ Queue for Remediation** - Add to remediation queue

**For QUEUED files:**
- **➖ Remove from Queue** - Reset to NONE (cancels the queue)

**For MISSING / FAILED / SKIPPED records:**
- **🗑️ Delete from SQLite Database** - Remove stale record
  - Only affects this tool's local `repair.db`
  - Does NOT touch files on disk or Radarr database

---

## CLI Commands

### Scan Library

```powershell
# Scan with defaults (2 workers, resumable)
pipenv run python main.py scan

# Scan with custom workers
pipenv run python main.py scan --workers 4

# Scan specific root
pipenv run python main.py scan --root "Z:\Movies\A-H"

# Rescan everything (ignore recent scans)
pipenv run python main.py scan --rescan

# Test scan (limit to 10 folders)
pipenv run python main.py scan --limit 10
```

### List Files

```powershell
# List all scanned files
pipenv run python main.py list

# Show only corrupt files
pipenv run python main.py list --corrupt

# Show queued files
pipenv run python main.py list --queued

# Sort by size
pipenv run python main.py list --sort size_bytes
```

### Queue for Remediation

```powershell
# Queue all corrupt files
pipenv run python main.py queue --all-corrupt

# Queue specific movie (partial name match)
pipenv run python main.py queue "28 Years Later"
```

### Execute Remediation

```powershell
# Dry run (preview without changes)
pipenv run python main.py remediate --dry-run

# Remediate all queued files
pipenv run python main.py remediate

# Limit batch size
pipenv run python main.py remediate --max 5
```

---

## Database

### Backend Options

The tool supports two database backends, selected via `DB_BACKEND` in `.env`:

#### **SQLite (default)** — single-PC use
- File: `repair.db` in the tool directory
- Tables: `files`, `runs`
- Set in `.env`: `DB_BACKEND=sqlite` (or omit — it's the default)

#### **PostgreSQL** — multi-PC distributed scanning
- Connection: shared LAN/Tailscale Postgres server
- Tables: `repair_files`, `repair_runs` (named differently to coexist with `Movie-Library-Compressor`)
- Configure in `.env`:
  ```
  DB_BACKEND=postgres
  DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
  WORKER_ID=ms01-a   # optional; defaults to hostname
  ```
- Auto-fallback: if `DATABASE_URL` host is unreachable, the tool tries the
  hosts listed in `config.py:POSTGRES_HOST_CANDIDATES` (LAN IP → Tailscale DNS → Tailscale IP)
- Tables auto-created on first connection
- Multiple PCs running the app share the same data

**Switching between backends:** simply change `DB_BACKEND` in `.env`. Each
backend has its own independent state (no auto-sync between them).

#### Migrating SQLite Data to PostgreSQL

If you've been using SQLite and want to switch to PostgreSQL without losing
your scan history, use the migration script:

```powershell
# Make sure DATABASE_URL is set (in .env or as env var)

# Step 1: Dry-run to see what would be copied
pipenv run python migrate_sqlite_to_postgres.py

# Step 2: Actually perform the migration
pipenv run python migrate_sqlite_to_postgres.py --execute

# Optional: Replace conflicting Postgres rows with SQLite versions
pipenv run python migrate_sqlite_to_postgres.py --execute --replace
```

What it does:
- Reads every row from SQLite `files` and `runs`
- Inserts them into Postgres `repair_files` and `repair_runs`
- Read-only on the SQLite source (your `repair.db` is never modified)
- Idempotent: safe to run multiple times (skips conflicts by default)
- Reports counts before and after so you can verify

After migrating, change `DB_BACKEND=postgres` in `.env` and your scan history
appears in the GUI exactly as before.

### Schema

#### `files` (SQLite) / `repair_files` (Postgres)
- `folder_path` - Unique path to movie folder
- `scan_state` - UNKNOWN | CLEAN | CORRUPT | ERROR | TIMEOUT | MISSING | EMPTY
- `remediation` - NONE | QUEUED | DELETED | RESEARCHING | REMEDIATED | FAILED | SKIPPED
- `stderr_tail` - Last 400 chars of ffmpeg stderr
- `last_scan_at` - timestamp
- `radarr_movie_id` - Radarr internal ID
- `worker_id` - (Postgres only) which PC scanned this folder
- `lock_until` - (Postgres only) reserved for distributed coordination (future use)

#### `runs` (SQLite) / `repair_runs` (Postgres)
- Audit trail of scan/remediate runs
- Stores stats: clean_count, corrupt_count, etc.
- `worker_id` - (Postgres only) which PC ran this scan

### Resumable Scans

- Scans are resumable by default
- Skips folders scanned within last 7 days
- Use `--rescan` to force re-scan all

---

## Troubleshooting

### "ffmpeg not found on PATH"

**Solution:**
1. Install ffmpeg: `winget install ffmpeg`
2. Or add to PATH: `C:\ffmpeg\bin`
3. Or place ffmpeg at: `C:\ffmpeg\bin\ffmpeg.exe`

### "Movie not found in Radarr"

**Cause:** Folder name doesn't match Radarr's library

**Solution:**
1. Check folder naming: `Movie Title (YYYY)`
2. Verify movie exists in Radarr
3. Add manually in Radarr first, then remediate

### Scan is slow

**Normal:** 30s-6min per movie depending on:
- File size
- NAS read speed
- Corruption location (early vs late in file)

**Speed up:**
- Increase workers: 4-8 for faster scanning
- Check NAS network speed
- Ensure no other heavy disk activity

### Files marked as TIMEOUT

**Cause:** Scan took longer than the per-file timeout (adaptive: 2 min/GB minimum).

**Important:** TIMEOUT does NOT mean the file is corrupt. The scan just gave up early.

**Solution:**
- TIMEOUT files are auto-rescanned on next scan (no manual action needed)
- If they keep timing out: huge 4K files on slow NAS - normal
- DO NOT queue TIMEOUT files for remediation (they may not be corrupt!)

### Files marked as MISSING

**Cause:** The folder no longer exists on disk (you deleted it, moved it, or NAS unavailable).

**Solution:**
- Right-click → "🔍 Verify Folder Exists" to recheck
- If truly gone: Right-click → "🗑️ Delete from SQLite Database" to clean up the record
- Database records are not auto-removed - keeps history of what you had

### App won't close

**During scan:**
- Click "Stop" button first
- Or click X → Confirm "Stop and exit"

**If frozen:**
- Task Manager → End Task
- Database changes are committed per-file (safe)

### "SQLite objects created in a thread..."

**Fixed in current version.** Each worker creates its own DB connection.

---

## Integration with Other Tools

### Pluck Movies

**Set VERIFY_LEVEL=3** in `config/movies.py`:
```python
VERIFY_LEVEL = 3  # Full ffmpeg null-decode
```

- Catches corruption before import
- Prevents re-acquiring bad files
- Completes the remediation loop

### Movie-Library-Compressor

Reads library roots from `compressor.yaml`:
```yaml
hosts:
  mforum-ms01-a:
    library_path: "Z:\\Movies\\A-H"
```

No need to configure library paths twice.

### Radarr Import from Staging Folder

Uses same `.env` configuration:
```bash
RADARR_URL=http://mforum-ms01-a:8989
RADARR_API=your-api-key-here
```

---

## Performance Guidelines

### Finding the Optimal Worker Count

The right number of parallel scans depends on your hardware: network speed,
NAS disk type (HDD vs SSD), and codec mix (HEVC is more CPU-intensive than
H.264). The default of 2 is a reasonable starting point for most setups.

To measure empirically, use the built-in benchmark:

```powershell
.\RepairBrokenMedia.exe benchmark
```

It scans a small sample at different worker counts (1, 2, 4 by default), uses
a temporary isolated SQLite database (your real data is not touched), and
prints a summary table showing the speedup curve.

**See [BENCHMARK.md](BENCHMARK.md) for full documentation**, including:
- What to stop before running
- How to interpret the output
- Common options (`--limit`, `--workers`, `--root`, `--max-file-gb`)
- Hardware-based recommendations if you don't want to benchmark
- Real-world example with explanation

### Bottleneck-Based Recommendations

If you don't want to benchmark, here are reasonable defaults:

| Network | Recommended Workers | Why |
|---|---|---|
| 1 GbE NAS link | **2** | Single link saturates around 100-125 MB/s |
| 2.5 GbE NAS link | **3-4** | More headroom for parallel reads |
| 10 GbE NAS link | **6-8** | CPU usually becomes the bottleneck before network |
| HDD-based NAS | **2-3** max | Disk seeks limit benefit of parallelism |
| SSD-based NAS | scale with network | Disk is rarely the bottleneck |

### Library-Wide Time Estimates

These assume **2 workers on 1 GbE** (the default). Faster networks scale roughly
linearly with worker count up to your bottleneck.

| Library Size | Workers | Expected Time |
|--------------|---------|---------------|
| ~500 movies  | 2       | 6-12 hours    |
| ~1000 movies | 2-4     | 8-24 hours    |
| ~3600 movies | 2-4     | 24-72 hours   |

For multi-PC scanning, divide by the number of PCs running in parallel
(coordination is automatic via the Postgres backend — see DEPLOYMENT.md).

### Scan Time Per File

- **Small (< 2 GB):** 30s - 1 min
- **Medium (2-5 GB):** 1-3 min
- **Large (5-15 GB):** 3-6 min
- **Very Large (15+ GB):** 6-15 min

The adaptive timeout adjusts automatically (2 minutes per GB), so 4K rips
that previously timed out at 30 minutes now have the room to complete.

---

## Best Practices

### Before First Scan

1. ✅ Test with `--limit 10` first
2. ✅ Verify Radarr connection
3. ✅ Check ffmpeg installation
4. ✅ Ensure library paths are accessible

### During Scanning

1. ✅ Leave app running (use right-click menu)
2. ✅ Queue corrupt files as you find them
3. ✅ Review ffmpeg logs before remediation
4. ✅ Monitor network/NAS load

### Before Remediation

1. ⚠️ Verify you want to DELETE these files
2. ⚠️ Check Radarr has active indexers
3. ⚠️ Ensure Pluck Movies VERIFY_LEVEL=3
4. ⚠️ Start with `--dry-run` to preview

### After Remediation

1. ✅ Wait for Radarr to search/download
2. ✅ Use Pluck Movies to import new copies
3. ✅ Re-scan to verify REMEDIATED state
4. ✅ Check logs for any FAILED remediations

---

## State Machine

### Scan States

```
[Folder Discovered]
        ↓
    UNKNOWN  (default)
        ↓
   [Scan runs]
        ↓
    ┌──────┬─────────┬────────┬─────────┬────────┬─────────┐
    ↓      ↓         ↓        ↓         ↓        ↓         ↓
  CLEAN CORRUPT   ERROR   TIMEOUT   MISSING   EMPTY    UNKNOWN
                                  (deleted)            (will retry)
```

**Auto-Rescan Logic:**
- CLEAN, CORRUPT, EMPTY, MISSING → Skip if scanned within 7 days (definitive)
- ERROR, TIMEOUT, UNKNOWN → Always rescan (failed attempts)


### Remediation States

```
  NONE  (default)
    ↓
 [User queues]
    ↓
  QUEUED
    ↓
 [Remediation starts]
    ↓
  DELETING  (file being deleted)
    ↓
  DELETED  (file removed from disk)
    ↓
 [Radarr workflow]
    ↓
  RESEARCHING  (Radarr searching)
    ↓
 [User re-scans after Pluck imports]
    ↓
    ┌─────────┴──────────┐
    ↓                    ↓
 REMEDIATED           FAILED
 (clean copy)      (re-corrupt)
```

**SKIPPED:** User manually marks as "don't remediate"

---

## FAQ

### Q: What corruption patterns does it detect?

**A:** 14+ patterns including:
- File ended prematurely
- Non-monotonically increasing DTS
- decode_slice errors
- Missing reference frames
- Invalid NAL unit size
- Truncated packets
- Corrupt EBML headers

Full list in `scanner.py:TROUBLE_KEYWORDS`

### Q: Does it delete files automatically?

**A:** No. Workflow requires:
1. Manual queue
2. Click "Delete + Re-search"
3. Confirm deletion dialog

### Q: Can I undo a remediation?

**A:** No. Files are permanently deleted. Only Radarr can re-acquire.

### Q: What if Radarr can't find a replacement?

**A:** File stays in RESEARCHING state. Radarr will keep trying based on its configuration.

### Q: Does it work with TV shows?

**A:** Not in v1. Movies only (Radarr API). Sonarr support is planned for v2.

### Q: Can I run multiple scans simultaneously?

**A:** No. One scan at a time. Use higher worker count instead.

### Q: What happens if I close the app during a scan?

**A:** 
- Confirmation dialog appears
- Option to stop scan and exit
- Database is safe (per-file commits)
- Scan can resume later

---

## Support

### Issues

Report bugs or feature requests at your GitHub repository.

### Logs

Located at: `logs/repair_<timestamp>.log` (if logging is enabled)

Database: `repair.db` (can be queried with any SQLite client)

### Configuration

- `.env` - Environment variables
- `config.py` - Library roots, DB path
- `compressor.yaml` - Library paths (inherited)

---

## Version History

**v1.0** (2026-06-17)
- Initial release
- Full scan + remediation workflow
- GUI with live progress
- CLI commands
- SQLite tracking
- Radarr integration

---

*Built for the Media Tools Consortium | Part of the Movie Library Management Suite*
