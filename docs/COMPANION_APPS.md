# Companion Apps — Compressor, Movie Fixer, Repair Broken Media Files

Three apps work the movie library together. Each does **one** job. This document
explains how they fit, so you always know which app to open for a given problem.

- **Movie Library Compressor** (the hub repo) — *compress*. Runs unattended and is
  the system's hub. It also *detects* corruption as a by-product.
- **Movie Fixer** (`../../Movie Fixer/`) — *triage the compressor's stuck rows*.
- **Repair Broken Media Files** (`../`) — *repair
  corrupt media* (scan → diagnose → delete → Radarr re-acquire).

The compressor never repairs files. When it flags something, the other two apps
are where you act.

---

## 1. The three apps at a glance

| App | One job | You use it when | Interface |
|---|---|---|---|
| **Movie Library Compressor** | Compress non-HEVC → HEVC, then verify the output | Always (scheduled, hands-off) | CLI + GUI launcher (`launcher.exe`) |
| **Movie Fixer** | Requeue or Skip Forever the compressor's `failed` / `exhausted` rows | A compressor row is stuck and won't clear | GUI (`movie-fixer.exe`) |
| **Repair Broken Media Files** | Deep-scan for structural corruption; delete + Radarr re-search | A file is (or may be) corrupt and needs replacing | GUI (`RepairBrokenMedia.exe`) + CLI |

---

## 2. Who owns what state

Understanding this stops you fighting the tools.

| Data | Owner | Who else reads/updates it |
|---|---|---|
| Compressor tracker rows (`compressed`/`skipped`/`failed`/`exhausted`) | Movie Library Compressor (`tracker.py`, SQLite or Postgres) | **Movie Fixer** reads and updates rows (Requeue/Skip) |
| `compressor.yaml` (library roots, thresholds) | Movie Library Compressor | **Movie Fixer** reads it for config; **Repair Broken Media Files** reads `library_path`/roots |
| Repair scan results (`repair.db`: CLEAN/CORRUPT/remediation state) | Repair Broken Media Files | Nobody else — its own audit trail |
| `.env` (Radarr URL/API, email, DATABASE_URL) | Per-app; each tool folder has its own copy | Radarr creds shared in format with Radarr Import; DB creds shared with the compressor |
| Library files on the NAS | You / Radarr | The compressor (reads), Repair Broken Media Files (deletes on your command) |

The two apps must **not** write the same store. Movie Fixer edits compressor
rows; Repair Broken Media Files edits its own `repair.db`. They meet through the
shared `compressor.yaml`/`.env` and (planned) the compressor's Postgres tracker.

---

## 3. The end-to-end lifecycle

```
                     ┌──────────────────────────────────────────────┐
                     │  Movie Library Compressor  (always running)  │
                     │  scan → decide → encode → verify → track      │
                     └───────────────┬──────────────────────────────┘
                                     │  post-encode verification
             ┌───────────────────────┼───────────────────────────┐
             ▼                       ▼                           ▼
        SUCCESS                  FAILED                    POSSIBLY_CORRUPTED
     (compressed)        (interrupted encode)            (source shows damage)
             │                       │                           │
             │                  retried each pass                │
             │                       │                    ┌──────▼───────────────────┐
             │                       ▼                    │  Repair Broken Media     │
             │               hits max_failures            │  Files                   │
             │                       │                    │  scan → Deep Inspect →   │
             │                       ▼                    │  delete + Radarr search  │
             │             ┌──────────────────┐           └──────┬───────────────────┘
             │             │   Movie Fixer    │                  │ Radarr downloads
             │             │  Requeue  /      │                  ▼ replacement
             │             │  Skip Forever    │          Pluck Movies (VERIFY_LEVEL=3)
             │             └──────────────────┘                  │ imports if clean
             │                                                   │
             └───────────────────────── re-scan confirms CLEAN ───┘
                            → compressor compresses it again
```

---

## 4. Routing: which app do I open?

Start from the signal you received.

| Signal / symptom | App | Action |
|---|---|---|
| Routine compression — nothing wrong | **Movie Library Compressor** | Let the scheduled task run (or launch the GUI for a manual pass) |
| `FAILED` email/digest row, source is fine | **Movie Library Compressor** | Do nothing — it retries automatically |
| `EXHAUSTED` row, no corruption evidence | **Movie Fixer** | Requeue (retry) or Skip Forever (retire) |
| Row whose source file is gone (`MISSING` in Movie Fixer) | **Movie Fixer** | Select Missing → likely a NAS mount issue, or Requeue |
| `POSSIBLY_CORRUPTED` row / "file ended prematurely" | **Repair Broken Media Files** | Scan that folder → Deep Inspect → Delete + Re-search |
| Suspect the whole library has bad files | **Repair Broken Media Files** | Full scan (weekly/sweep), then work Group A / Group B |
| Not sure whether a file is truly broken | **Repair Broken Media Files** | Deep Inspect (fast) → Full Deep Decode (if ambiguous) |
| Replacement "Imported" by Radarr | **Repair Broken Media Files** | Re-scan to confirm CLEAN (Radarr does *not* verify playback) |

---

## 5. Typical workflows

### 5.1 Routine (hands-off)
The scheduled `Compressor Compress` task runs `compressor.exe compress --loop` on
each host. Nothing to do. Read the daily/overnight digest for a summary. Only
react to failures per §4.

### 5.2 Responding to a failure notification
1. Open [FAILURE_RUNBOOK.md](../../Movie Library Compressor/docs/FAILURE_RUNBOOK.md) and classify the failure
   (real file problem vs infrastructure noise vs source corruption).
2. Infrastructure skip → no action.
3. `failed` → wait (auto-retry). `exhausted` → Movie Fixer.
4. `POSSIBLY_CORRUPTED` → Repair Broken Media Files.

### 5.3 Weekly corruption sweep
In **Repair Broken Media Files**: **Start Scan** over the libraries → for CORRUPT
rows run **Re-search all Group A** (re-download fixes it) or **Inspect all Group
B** (different release needed) → **Check Re-downloads** → **Re-scan** the
imported ones to confirm CLEAN. Files that come back CLEAN are re-compressed by
the compressor on its next pass.

### 5.4 After repairing a file
The new file is a non-HEVC source again → the compressor picks it up and
compresses it. No separate step; the tracker row (if any) is unrelated to the
replacement file path.

---

## 6. Integration points (how they share)

- **`compressor.yaml`** — Repair Broken Media Files reads library roots from it
  automatically; Movie Fixer reads it (and the sibling `.env`) for config.
- **Postgres tracker** — the compressor writes rows; Movie Fixer reads/updates
  them. Repair Broken Media Files' roadmap plans to read the tracker to skip
  folders already known-good and reuses the compressor's Postgres
  connection-fallback pattern (LAN → Tailscale DNS → Tailscale IP).
- **Radarr** — Repair Broken Media Files drives delete + re-search; Pluck Movies
  runs `VERIFY_LEVEL=3` so a bad replacement is caught before import.
- **`.env`** — Radarr and DB credentials follow the consortium-wide key
  conventions (see `../../Movie Tools Launcher/README.md`).

---

## 7. Where the apps live (deployed)

All three are tiles in the **Media Tools / Movie Tools Launcher**, and each also
has its own source repo:

| App | Tile / folder | Executable |
|---|---|---|
| Movie Library Compressor | `Movie Library Compressor/` | `launcher.exe` (GUI) — spawns `compressor.exe` |
| Movie Fixer | `Movie Fixer/` | `movie-fixer.exe` |
| Repair Broken Media Files | `Repair Broken Media Files/` | `RepairBrokenMedia.exe` |

Worker PCs run **only** the compressor (`C:\Compressor\compressor.exe` via
scheduled task, auto-pulled from `Z:\Compressor-Deploy\`). Movie Fixer and Repair
Broken Media Files are interactive tools you run on the dev PC.

---

## 8. Golden rules

1. **The compressor detects; it does not repair.** Corruption → Repair Broken
   Media Files, stuck retries → Movie Fixer.
2. **Don't requeue a corrupt source.** The compressor fails on the same bytes
   again — repair (or re-acquire) the file.
3. **"Radarr Imported" ≠ healthy.** Only a re-scan proves a file is good.
4. **Infrastructure skips are not failures.** NAS/SMB hiccups don't consume the
   retry budget and need no action.
5. **One writer per store.** Movie Fixer edits compressor rows; Repair Broken
   Media Files edits its own DB.

---

## 9. Related documentation

- **[FAILURE_RUNBOOK.md](../../Movie Library Compressor/docs/FAILURE_RUNBOOK.md)** — triage a reported compressor
  failure and route it to the right app.
- **[MEDIA_FILE_INTEGRITY_CHECKING.md](../../Movie Library Compressor/docs/MEDIA_FILE_INTEGRITY_CHECKING.md)** — the
  compressor's check chain and how corruption is detected.
- **[USERGUIDE.md](../../Movie Library Compressor/docs/USERGUIDE.md)** — compressor statuses, emails, configuration.
- `../../Movie Fixer/USERGUIDE.md` — Requeue / Skip Forever.
- `../README.md` and `CHEATSHEET.md` — the
  corruption scan → fix → verify loop.
