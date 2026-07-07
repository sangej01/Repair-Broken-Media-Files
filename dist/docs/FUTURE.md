# Future Enhancements: Repair Broken Media Files

Roadmap and ideas for future versions. Not committed to specific timelines.

---

## Recently Completed

### ✅ PostgreSQL Backend + Distributed Scan Coordination (Phase 1-3 done)

The dual-backend infrastructure plus atomic claim semantics is in place.
Multiple PCs can now run scans simultaneously against the same library
without duplicating work.

Switch via `DB_BACKEND` in `.env`:
- `sqlite` (default) → local `repair.db`
- `postgres` → shared `casaos` database, tables `repair_files` / `repair_runs`

What's done:
- Abstract dispatch via `RepairDBConnection` wrapper in `db.py`
- Both backends implement identical operations (upsert, get, mark_*, etc.)
- Postgres host fallback (LAN → Tailscale DNS → Tailscale IP) lifted from compressor
- `worker_id` and `lock_until` columns in both backends
- SQLite auto-migration: ALTER TABLE adds new columns to existing databases
- Schema auto-migration: tables auto-created on first connect
- **Atomic claim/release for distributed scanning** (`db.claim_for_scan`,
  `db.release_scan_claim`)
- **Stale lock cleanup** on scan startup (per-worker)
- **`SCANNING` status** visible in GUI showing which folders are in flight
- Tested with both backends (7 claim/release scenarios verified)

What's still pending (Phase 4-5 below):
- Headless `python main.py worker` mode (no GUI required)
- GUI per-worker activity panel (see all PCs' progress in real time)

---

## High Priority

### Multi-PC Distributed Scanning — Phase 3-5

**Problem:** Scanning a 3,600-movie library on a single PC takes 24-48 hours. With multiple PCs (ms01-a, geekom-gt1-a, beelink-nyc, etc.) sitting idle, we could parallelize across machines.

**Approach: Lift the proven pattern from `Movie-Library-Compressor/tracker.py`**

That tool already implements exactly what we need: a pluggable backend (SQLite or PostgreSQL) where multiple hosts share a Postgres database. We can copy its design directly.

#### Reference Implementation

`Movie-Library-Compressor/src/compressor/tracker.py` does this with:

1. **Abstract base class** `TrackerDB` defines the interface
2. **`SQLiteTrackerDB`** - default, single-PC (per-host file)
3. **`PostgresTrackerDB`** - shared LAN database
4. **`create_tracker(cfg)` factory** picks backend based on config
5. **Hostname-aware records** so each PC's work is identifiable
6. **Multi-host fallback** for connection (LAN IP → Tailscale DNS → Tailscale IP)

#### Adapt for Repair Broken Media Files

**Simpler than Compressor's approach:**
- Don't use compressor.yaml — use plain `.env` (consistent with Pluck Movies, Radarr Import patterns)
- All config in `.env`, no YAML

**Proposed `.env` additions:**
```bash
# Database backend: 'sqlite' (default) or 'postgres'
DB_BACKEND=sqlite

# Only needed when DB_BACKEND=postgres
DATABASE_URL=postgresql://repair_user:password@host:5432/repair_broken_media

# Optional: try multiple hosts in order (first to connect wins)
POSTGRES_HOST_CANDIDATES=192.168.1.238,casaos,100.102.164.45

# Optional: override the hostname used for worker tracking
# Defaults to socket.gethostname()
WORKER_ID=ms01-a
```

#### Implementation Plan

**Phase 1: Abstract the Database Layer (~4h)** ✅ DONE
- ~~Create db_interface.py with RepairDB abstract class~~
- ~~Refactor db.py → db_sqlite.py (lift current code as-is)~~
- ~~Add db_postgres.py (port db_sqlite.py to psycopg2)~~
- ~~Factory function create_db(env) chooses backend from DB_BACKEND~~

**Phase 2: PostgreSQL Schema (~2h)** ✅ DONE
- ~~Same files and runs tables as SQLite~~
- ~~Add worker_id column to files table~~
- ~~Add lock_until TIMESTAMP for distributed coordination~~
- ~~Use ON CONFLICT (folder_path) DO UPDATE for upserts~~ (uses SELECT-then-INSERT/UPDATE for now; can optimize later)

**Phase 3: Distributed Scan Coordination (~6h)** ✅ DONE
Implemented as `db.claim_for_scan(conn, folder_path, worker_id, lock_minutes=60)`:
```sql
INSERT INTO repair_files (folder_path, worker_id, lock_until, scan_state, first_seen_at)
VALUES (%s, %s, %s, 'SCANNING', %s)
ON CONFLICT (folder_path) DO UPDATE
    SET worker_id = EXCLUDED.worker_id,
        lock_until = EXCLUDED.lock_until,
        scan_state = 'SCANNING'
    WHERE repair_files.lock_until IS NULL
       OR repair_files.lock_until < NOW()
       OR repair_files.worker_id = EXCLUDED.worker_id
RETURNING worker_id, scan_state
```
- Atomic claim, no two workers grab same folder
- Lock auto-expires after 60 minutes (handles dead workers)
- Same worker can refresh own lock (long-running scans)
- Scanner releases lock + writes final result via `db.release_scan_claim()`
- 7 claim/release scenarios verified in test suite

**Phase 4: Worker Mode (~4h)**
- New CLI: `python main.py worker` runs headlessly
- Polls PostgreSQL for unclaimed work
- Scans, updates DB, repeats until idle
- Logs to `logs/worker_<hostname>.log`
- Can run as Windows scheduled task on each PC

**Phase 5: GUI Updates (~6h)**
- View worker activity column showing which PC scanned each folder
- Per-worker statistics panel (movies/hour throughput)
- Status bar: "3 workers active: ms01-a (scanning 28YL), gt1-a (scanning Naked), nyc (idle)"
- Cancel/pause specific workers (set lock_until to far future)

#### Connection Fallback Pattern (from compressor)

Lift this verbatim - it handles both LAN and Tailscale gracefully:

```python
# Try each host in POSTGRES_HOST_CANDIDATES until one connects
for host in POSTGRES_HOST_CANDIDATES:
    dsn = substitute_host(DATABASE_URL, host)
    try:
        return PostgresRepairDB(dsn, hostname)
    except Exception:
        continue
raise ConnectionError("Could not reach PostgreSQL via any host")
```

#### Benefits
- 3-4x faster library scanning with 4 workers across PCs
- Single source of truth (no SQLite syncing headaches)
- GUI runs anywhere, sees same data
- Workers can be added/removed dynamically
- Already have PostgreSQL running for `Movie-Library-Compressor`
- **Reuses proven pattern** - tracker.py has been running in production

#### Backwards Compatibility
- `DB_BACKEND=sqlite` (default) keeps current behavior unchanged
- Single-PC users see no change
- Multi-PC users opt in via .env change

#### Effort Estimate
- Phase 1 (abstraction): ~4 hours
- Phase 2 (schema): ~2 hours
- Phase 3 (coordination): ~6 hours
- Phase 4 (worker mode): ~4 hours
- Phase 5 (GUI updates): ~6 hours
- **Total: ~22 hours** (could be less by directly lifting tracker.py code)

#### Files to Reference
- `Movie-Library-Compressor/src/compressor/tracker.py` — the pattern to copy
- `Movie-Library-Compressor/src/compressor/config.py` — POSTGRES_HOST_CANDIDATES handling

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
