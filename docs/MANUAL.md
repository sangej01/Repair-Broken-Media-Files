# Repair Broken Media Files — Manual

Scenarios, workflows, CLI reference, and troubleshooting.

- New to the window? See [INTERFACE.md](INTERFACE.md) for every control explained.
- Want a quick task checklist? See [WORKFLOW_CHECKLIST.md](WORKFLOW_CHECKLIST.md).
- Beginner? Start with [IDIOTS_GUIDE.md](IDIOTS_GUIDE.md).

---

## 1. Mental model

The app scans your movie library by **fully decoding each video with ffmpeg**, catches
files that are structurally broken (corruption a quick header check would miss), and
helps you replace the bad ones via Radarr.

Every movie has **two independent states**:

| Column | Question it answers | Who sets it |
|--------|---------------------|-------------|
| **Status** | *Is this file OK?* (the diagnosis) | the scanner |
| **Remediation** | *What have I done about it?* (the fix pipeline) | you + Radarr |

They move independently. `CORRUPT` (Status) + `RESEARCHING` (Remediation) at the
same time is completely normal — it means the file was broken, you deleted it, and
Radarr is downloading a replacement.

---

## 2. The core loop

Most sessions are one loop: **scan → diagnose → remediate → verify.**

```
Start Scan
  ↓
Status?
  CLEAN      → done
  TIMEOUT    → Re-scan TIMEOUTs (usually transient NAS stall)
  ERROR      → fix environment (ffmpeg/PATH/permissions), re-scan
  CORRUPT    → Deep Inspect
                 ↓
              Truly broken?
                No  → Full Deep Decode → Mark CLEAN (was a false positive)
                Yes → Queue → Delete + Re-search (Radarr grabs a fresh copy)
                               ↓
                            Check Re-downloads
                               ↓
                            Imported? → Re-scan Imported
                               ↓
                            CLEAN → fixed
                            CORRUPT again → bad release; get a DIFFERENT one
```

---

## 3. Scenario: first run / setup

**Before starting:**
- The window title shows the running version (or run `RepairBrokenMedia.exe version`).
  If it's old, rebuild the exe with `build.ps1`.
- **ffmpeg/ffprobe must be on PATH** — scans and inspections fail if they aren't.
- Radarr URL and API key must be set in `.env` — remediation fails without them.
- The app opens in **Database view** showing any existing scan history.

The exe reads `repair.db` from the folder next to it (or from `REPAIR_DB_PATH` in
`.env` if you need to point it at a different location — useful when running both
the exe and source side by side).

---

## 4. Scenario: full library scan (first time and resuming)

1. Select libraries (A-H / I-S / T-Z checkboxes) and set **Parallel scans** and
   **Timeout/file**.
2. Click **Start Scan** — the app automatically switches to Live view and pre-loads
   known rows so you see the table immediately rather than a blank screen.
3. Per-worker lines appear below the progress bar showing each file in flight with a
   live elapsed timer.
4. Results accumulate in the Scan Activity log and in the database in real time.
5. **Stopping is safe.** In-flight files are reset to UNKNOWN — never falsely
   marked CORRUPT. Scan can resume from where it left off.
6. **Resuming is fast.** Files with a definitive verdict whose size+mtime are
   unchanged are skipped (they already have an answer). Use `scan --rescan` to
   force a full re-decode.

A full decode of a large library over a NAS is a **multi-day job**. The Library
coverage bar (scoped to selected libraries) is your long-run progress indicator.

---

## 5. Scenario: CORRUPT files — diagnose before you act

**Don't delete on the CORRUPT flag alone.** The Reason column triage label tells you
which group the file is in, which determines what to do:

### Group A — re-download friendly

Reason label starts with one of:
`[Incomplete / truncated]` · `[Missing reference frames]` · `[No decodable frames]` ·
`[Partial corruption (concealed)]` · `[Generic corruption]`

These are download accidents. A fresh copy of the **same release** fixes them.
No need to Deep Inspect — the label already tells you the answer.

**One at a time:** check the row → **Delete + Re-search**.
**Batch:** set Corruption type → **A (re-download)** → **Re-search all Group A** →
confirm the list → Yes.

### Group B — check before acting

Reason label starts with one of:
`[Broken container (MKV)]` · `[Encoder artifact (H.264/H.265)]` ·
`[Encoder artifact (slice decode)]` · `[Malformed packet]` ·
`[Timestamp (DTS/PTS) problem]`

These often mean the source release is fundamentally bad. Re-downloading the
same release will likely reproduce the same broken file — so **do not** use the
plain **Delete + Re-search** button on Group B files. (If you do, a safety guard
warns you and offers to blocklist + search for a different release instead.)

**One at a time:** right-click → **Deep Inspect** → follow the offered action:
- **Delete + Re-search** (truncated / fixable)
- **Delete + Blocklist + Re-search** (bad source — Radarr finds a *different* release)
- **Run Full Deep Decode** (ambiguous — header and tail both clean)

If the Full Deep Decode returns a **CLEAN** verdict (zero real errors), the CORRUPT
flag was a false positive. The report dialog offers **Mark CLEAN in database** —
click it to record the clean verdict directly, without a costly full re-scan.

**Batch:** set Corruption type → **B (source damage)** → **Inspect all Group B** →
a cancelable progress dialog runs Deep Inspect on each file sequentially. When done:

1. **Bad-source confirm dialog appears first** — lists files the app determined are
   unfixable. Clicking Yes: deletes them, blocklists the bad release in Radarr, and
   triggers a search for a *different* release.
2. **Fixable confirm dialog appears second** (after the bad-source worker finishes,
   not simultaneously) — lists files that are re-downloadable. Clicking Yes:
   deletes them and re-searches the same release.
3. **Summary dialog** (read-only) — lists any inconclusive or error results that
   need manual review.

> **If both groups are present:** say Yes to the bad-source confirm, then wait for
> those rows to go RESEARCHING before the fixable confirm appears. If you need to
> act on them in separate passes, check only the rows you want first before clicking
> Inspect all Group B.

> **Blocklist caveat:** if a movie was manually imported (no Radarr "grabbed" history
> record), the blocklist step can't find a history entry. The worker falls back to a
> plain re-search and logs a warning. Handle that movie manually in Radarr if needed.

---

## 6. Scenario: remediate and verify (the full replace loop)

1. Filter Status = **CORRUPT**, Remediation = **Any**.
2. Deep Inspect anything uncertain (Group B especially).
3. For Group A: check rows → **Queue for Remediation** → **Delete + Re-search** →
   confirm → Yes. For Group B: use the batch button or right-click per-file.
4. Rows move to **RESEARCHING** (grayed/italic). Radarr searches and downloads.
5. Click **Check Re-downloads** to see which have arrived without opening Radarr:
   - **Imported** → Radarr has a file on disk.
   - **Downloading** → still in progress.
   - **Pending** → still searching.
   - **Not found in Radarr** → not a Radarr-managed title; handle manually.
6. Right-click the **Imported** ones → **Re-scan** to get a fresh verdict.
   - **CLEAN** → fixed.
   - **CORRUPT again** → Radarr re-grabbed the same bad release. Use
     **Delete + Blocklist + Re-search** (or Inspect all Group B) so Radarr finds
     a *different* release.

> **Critical:** "Radarr Imported" does **not** mean the file is clean. Radarr
> matches metadata and quality — it never decodes the video. The Re-scan step is
> the real health check that flips CORRUPT → CLEAN.

---

## 7. Scenario: TIMEOUTs

TIMEOUT is **not** a verdict — it means the decode didn't finish within the time
budget, almost always because of transient NAS I/O slowness or a very large file.

1. Click **Re-scan TIMEOUTs**.
2. Most come back **CLEAN**. If one keeps timing out, try a longer **Timeout/file**
   setting or check NAS health.
3. If a retry comes back **CORRUPT**, it's real corruption — diagnose and remediate.

The completion summary reports every outcome, including a **TIMEOUT** count. If it
shows something like `Folders scanned: 43, CLEAN: 0, CORRUPT: 0, ... TIMEOUT: 43`,
that means every file *timed out again* — none finished decoding in the current
budget. That's expected for large 4K/HEVC rips over a slow NAS; raise
**Timeout/file** (e.g. 2 hr or No limit) and re-scan, or investigate NAS speed.
A TIMEOUT is never a corruption verdict — the files are just slow, not broken.

The timeout is duration-aware (long low-bitrate films get a proportional budget).
A stall detector flags reads that make no progress for ~5 minutes as
"STALLED near X" rather than letting them run indefinitely.

---

## 8. Scenario: "Not found in Radarr" (FAILED remediation)

Some titles aren't managed by Radarr (manually imported, or a cut/edition that
shares a TMDB ID with the main film). Remediation FAILS with
"Movie not found in Radarr".

**Options:**
- If you don't want the file: delete it manually → right-click → **Delete from
  SQLite Database** to remove the record.
- If you want a clean copy: add the movie to Radarr (or acquire manually), place
  the good copy in the folder, then right-click → **Re-scan** to confirm CLEAN.
- Right-click → **Mark as Skipped** to stop it appearing in batch runs.

**Note on path matching:** the app matches by folder name (case-insensitive, with
punctuation normalisation — dots → spaces, apostrophes stripped). If a movie still
fails to match, check that the folder name on disk corresponds reasonably to what
Radarr shows as the movie's path.

---

## 9. Scenario: backups and recovery

`repair.db` is the one irreplaceable file — it holds all scan results, remediation
state, the activity log, and the mtime fingerprints that keep resumed scans fast.
Losing it means re-scanning the whole library.

**Backup happens automatically on app exit.** You can also click **Backup DB** at
any time. Backups are consistent SQLite `.backup` snapshots (safe while the app is
running) stored as timestamped copies in `Z:\Repair Media File Deploy\db-backups\`
(override with `REPAIR_DB_BACKUP_DIR` in `.env`). The newest ~30 are kept.

**To restore:** close the app, copy the desired
`repair-YYYYMMDD-HHMMSS.db` file over `repair.db`, reopen.

---

## 10. Which statuses should I re-scan?

Re-scanning only changes the outcome for states that were never a real verdict.

| Status | Re-scan? | Why |
|--------|----------|-----|
| **TIMEOUT** | **Yes** — Re-scan TIMEOUTs button | Environmental (slow/hung NAS), not a verdict |
| **UNKNOWN** | **Yes** | Interrupted / never decided |
| **ERROR** | **Yes, after fixing the cause** | Infrastructure failure, not the file |
| CORRUPT | No — use **Deep Inspect** instead | Deterministic on the same bytes; re-decoding gives the same answer |
| CLEAN | No | Definitive pass |
| EMPTY | Only if you added a video | No video was present |
| MISSING | Use "Verify Folder Exists" | Folder-existence check, not a decode |

The **Problematic** status filter selects TIMEOUT + UNKNOWN + ERROR in one click.

---

## 11. Identifying systemic issues (Attempts column)

The **Attempts** column tracks remediation attempts. **Bold orange at 2, bold red
at 3+** — stop and investigate before trying again.

| Pattern | Likely cause | Action |
|---------|-------------|--------|
| One movie, many attempts | Indexer keeps grabbing the same bad release, or the source encode is bad | Deep Inspect → if BAD SOURCE, use Delete + Blocklist + Re-search; Radarr finds a different release |
| Many movies, all attempts = 2, all newly corrupt | Pluck rsync silently corrupting during NAS transfer, or SAB/par2 silent failure | Stop bulk remediation; investigate Pluck VERIFY_LEVEL=3 and rsync integrity |
| Attempts = 1, then CLEAN | Normal healthy cycle | Keep going |

---

## 12. Best practices

**Before first remediation:**
1. Test with `scan --limit 10` to confirm ffmpeg works and library paths are accessible.
2. Verify Radarr connection (correct URL and API key in `.env`).
3. Set Pluck Movies `VERIFY_LEVEL=3` to catch corruption before import.
4. Try one movie first; confirm the workflow works end-to-end.

**Batch size:**
Don't queue too many at once. 10–20 is manageable; 50+ can overwhelm Radarr's indexers.

**One scanner at a time:**
Never start a second scan (GUI or CLI) while one is already running — concurrent SQLite
writers collide and crash the app. Deep Inspect and Inspect all Group B are read-only
and safe during a scan, but do not click Start Scan.

---

## 13. CLI reference

All commands work with both `pipenv run python main.py <cmd>` (source) and
`RepairBrokenMedia.exe <cmd>` (built exe).

```
scan        [--workers N] [--root PATH ...] [--rescan] [--limit N]
rescan-corrupt  [--states CORRUPT,TIMEOUT,...] [--workers N] [--limit N] [--dry-run]
list        [--corrupt | --clean | --error | --empty | --queued]
            [--sort size_bytes | folder_path | last_scan_at]
queue       [--all-corrupt | <folder name> ...]
remediate   [--dry-run] [--max N]
benchmark   [--workers N ...] [--limit N] [--root PATH] [--max-file-gb N]
version
```

A CLI scan **refuses to run** while the GUI (or another scanner) holds the lock,
and vice-versa — this prevents two writers hanging or corrupting the SQLite DB.

See [BENCHMARK.md](BENCHMARK.md) for how to find your optimal worker count.
See [DEPLOYMENT.md](DEPLOYMENT.md) for building the exe and multi-PC setup.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| App "didn't launch" | Window opened buried in Alt-Tab | Alt-Tab / click taskbar icon |
| Table empty on startup | Wrong view mode | Set View → **Database (Show All Results)**, Status = **All** |
| Corruption type filter not visible | Window too narrow | Widen the window; the filter is to the right of Remediation |
| UI lags 4–5 s per click | NAS latency blocking a UI-thread read | Transient; check Z: health; restart app if persistent |
| Both progress bars stuck at 0% | ffmpeg hung on a NAS read | Stall detector flags it at ~5 min; or Stop + re-scan |
| "Scanner Busy" | Another scanner holds the lock | Close the other instance; stale locks auto-clear after expiry |
| "Movie not found in Radarr" | Folder name doesn't match Radarr's library | See Scenario 8 above |
| Movie re-downloaded but still CORRUPT | Radarr re-grabbed the same bad release | Deep Inspect → BAD SOURCE → Delete + Blocklist + Re-search |
| Lots of false TIMEOUTs | Long low-bitrate films or flaky NAS | Fixed by duration-aware timeout (v1.7.1+); re-scan with longer Timeout/file |
| "ffmpeg not found on PATH" | ffmpeg not installed or not on PATH | `winget install ffmpeg` or add `C:\ffmpeg\bin` to PATH |
| Remediation FAILED | Various — check the log | Right-click the row; log shown in Show ffmpeg Log, or query `repair.db` directly |
| App won't close during scan | Scan still running | Click Stop first, or click X → confirm "Stop and exit" |

---

*Part of the Media Tools Consortium.*
*See [INTERFACE.md](INTERFACE.md) for the full control reference.*
*See [DEPLOYMENT.md](DEPLOYMENT.md) for building the exe and multi-PC scanning.*
