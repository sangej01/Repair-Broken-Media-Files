# Future Enhancements: Repair Broken Media Files

Roadmap and ideas for future versions. Not committed to specific timelines.

---

## High Priority

### Multi-PC Distributed Scanning (PostgreSQL Backend)

**Problem:** Scanning a 3,600-movie library on a single PC takes 24-48 hours. With multiple PCs (ms01-a, geekom-gt1-a, beelink-nyc, etc.) sitting idle, we could parallelize across machines.

**Approach: Centralize the database in PostgreSQL**

#### Current Architecture (v1)
```
┌──────────────────┐
│   PC #1 (Local)  │
│  ┌───────────┐   │
│  │ App + GUI │   │
│  └─────┬─────┘   │
│        │         │
│  ┌─────▼─────┐   │
│  │ repair.db │   │
│  │ (SQLite)  │   │
│  └───────────┘   │
└──────────────────┘
```

#### Proposed Architecture (v2)
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ PC #1 (GUI)  │  │ PC #2 (Worker)│ │ PC #3 (Worker)│
│ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │
│ │  App+GUI │ │  │ │  Scanner │ │  │ │  Scanner │ │
│ └────┬─────┘ │  │ └────┬─────┘ │  │ └────┬─────┘ │
└──────┼───────┘  └──────┼───────┘  └──────┼───────┘
       │                 │                 │
       └─────────┬───────┴─────────────────┘
                 │
        ┌────────▼─────────┐
        │   PostgreSQL DB   │
        │ (centralized state)│
        └───────────────────┘
```

#### Implementation Plan

**Phase 1: Abstract the Database Layer**
- Create `db_interface.py` that defines abstract operations
- Refactor `db.py` → `db_sqlite.py` (current implementation)
- Add `db_postgres.py` (new implementation)
- Use config flag to choose backend:
  ```python
  DB_BACKEND = os.getenv("DB_BACKEND", "sqlite")  # or "postgres"
  ```

**Phase 2: PostgreSQL Schema**
- Same schema as SQLite but with PostgreSQL types
- Add `worker_id` column to track which PC scanned each folder
- Add `lock_until` column for distributed scan coordination
- Connection via existing PostgreSQL instance

**Phase 3: Distributed Scan Coordination**
- Each worker queries: `SELECT folder_path FROM files WHERE scan_state IN ('UNKNOWN', 'TIMEOUT', 'ERROR') AND (lock_until IS NULL OR lock_until < NOW()) LIMIT 1 FOR UPDATE SKIP LOCKED`
- This atomically claims a folder and skips it for other workers
- Lock expires after timeout (e.g., 1 hour) so dead workers don't block forever
- Worker updates `worker_id`, `last_scan_at`, `scan_state` when done

**Phase 4: Worker Mode**
- New CLI: `python main.py worker` runs headlessly
- Pulls work from PostgreSQL until library complete
- No GUI needed
- Can be scheduled task on each PC

**Phase 5: GUI Updates**
- View worker activity (which PC is scanning what)
- Per-worker statistics (movies/hour throughput)
- Cancel/pause specific workers from main GUI

#### Configuration Example
```bash
# .env
DB_BACKEND=postgres
POSTGRES_HOST=mforum-ms01-a
POSTGRES_PORT=5432
POSTGRES_DB=repair_broken_media
POSTGRES_USER=repair_user
POSTGRES_PASSWORD=...
WORKER_ID=ms01-a  # unique per machine
```

#### Benefits
- 3-4x faster library scanning with 4 workers across PCs
- Single source of truth (no SQLite syncing headaches)
- GUI runs anywhere, sees same data
- Workers can be added/removed dynamically
- Already have PostgreSQL running for `Movie-Library-Compressor`

#### Potential Issues
- Network latency on small queries (mitigated: rare during long scans)
- PostgreSQL becomes single point of failure (mitigated: backup to SQLite mirror?)
- Coordination edge cases (lost workers, stale locks)

#### Effort Estimate
- Phase 1 (abstraction): ~4 hours
- Phase 2 (schema): ~2 hours
- Phase 3 (coordination): ~6 hours
- Phase 4 (worker mode): ~4 hours
- Phase 5 (GUI updates): ~6 hours
- **Total: ~22 hours of focused work**

---

## Medium Priority

### Email Summaries
- Send HTML email after each scan or remediation run
- Statistics, failures, attempts breakdown
- Reuse `notify.py` pattern from sibling tools (Pluck, Radarr Import)
- Gated by `EMAIL_NOTIFY` flag

### Blacklist Bad Releases in Radarr
When remediation finds repeated corruption from same release:
- Auto-blacklist via `POST /api/v3/blocklist`
- Requires parsing Radarr's history API to identify the release
- Prevents Radarr from grabbing same bad release again
- Triggered when Attempts >= 3 (configurable)

### Auto-rescan After Pluck Imports
- Hook into Pluck Movies completion
- Automatically scan newly-imported movies
- Closes the verification loop
- Could use file system watcher or webhook from Pluck

### TV Shows / Sonarr Support
- Currently movies-only (Radarr)
- Add Sonarr API client
- Different API but similar patterns
- New scan target: episode files instead of movies
- Per-episode tracking in database

### Pre-scan Filter Against Compressor's Skip List
- `compressor.yaml` has `scanner.ignore_folders` (Extras, Sample, etc.)
- Read same list to avoid scanning known-skip folders
- Small optimization but more consistent with sibling tools

### Service Mode for Unattended Overnight Scans
- Run as Windows service or scheduled task
- No GUI required
- Logs to file
- Can be combined with worker mode (Phase 4 above)

---

## Low Priority / Nice to Have

### Per-Worker Statistics in GUI
- Movies scanned per hour
- Network throughput
- Average scan time per file size
- Useful for identifying slow workers / NAS issues

### Resume Mid-File Scan
- Currently if app quits mid-scan, that movie restarts from byte 0
- Track ffmpeg's progress (frame count) and resume?
- Probably not worth the complexity since ffmpeg can't actually resume

### Visual Diff of Scan Results
- Compare two scan runs side by side
- "What changed since last week?"
- Useful for trend analysis

### CLI Improvements
- `repair status` - quick overview without launching GUI
- `repair stats` - statistics dashboard in terminal
- `repair export` - dump database to CSV/JSON for analysis

### Better ffmpeg Output Parsing
- Categorize corruption types (truncation vs DTS issues vs codec errors)
- Severity ratings
- Suggest specific remediation strategies based on error type

### Integration with Movie-Library-Compressor's PostgreSQL Tracker
- Read existing tracker to skip "we already know this is fine" folders
- Speed up scans by leveraging existing data
- Combined with Phase 1-5 above, this is natural

---

## Out of Scope (For Now)

### File Repair (Actually Fixing Corruption)
- Not feasible for most corruption types
- Re-acquisition is the right approach
- ffmpeg `-c copy -bsf:a aac_adtstoasc` won't fix demuxer-level issues

### Automatic Indexer Source Switching
- Beyond Radarr's responsibility
- Would require complex indexer-aware logic

### Web Interface
- Native PySide6 GUI is sufficient
- Web UI adds complexity without clear benefit
- Could revisit if multi-PC mode (Phase 1-5) works well

---

## Architecture Notes

### Why PostgreSQL for Multi-PC?
- Already running for `Movie-Library-Compressor`
- ACID transactions handle race conditions
- `SELECT FOR UPDATE SKIP LOCKED` is the gold standard for distributed work queues
- Easy backup/restore
- Works across LAN (Tailscale, etc.)

### Why Not Just a NAS-Hosted SQLite?
- SQLite has poor concurrent-write support
- Network filesystems break SQLite locking semantics
- One worker would block others on writes
- Already considered and rejected

### Why Not Redis?
- Adds another moving piece
- PostgreSQL handles the same use case fine
- We don't need millisecond latency

---

## Contributing Ideas

If you have ideas for future enhancements, open an issue at:
https://github.com/sangej01/Repair-Broken-Media-Files/issues

---

*This document tracks roadmap. See [USERGUIDE.md](USERGUIDE.md) for current functionality.*
