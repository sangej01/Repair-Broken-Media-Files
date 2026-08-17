# Interface Reference: Repair Broken Media Files

Every control in the window, what it does, and when to use it.

For step-by-step tasks see [WORKFLOW_CHECKLIST.md](WORKFLOW_CHECKLIST.md).
For beginner orientation see [IDIOTS_GUIDE.md](IDIOTS_GUIDE.md).
For scenarios, CLI, and troubleshooting see [MANUAL.md](MANUAL.md).

---

## Window layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Repair Broken Media Files  v1.x.x                                         │
│  Scan your movie library for structurally broken files and remediate them   │
├────────────────────────────────────────────────────────────────────────────┤
│  View: [Database (Show All Results) ▾]              💾 info message         │
├────────────────────────────────────────────────────────────────────────────┤
│  Library: ☑ A-H  ☑ I-S  ☑ T-Z   Parallel scans:[2▾]  Timeout/file:[30min▾] │
│                                                   [Start Scan]  [Stop]      │
├────────────────────────────────────────────────────────────────────────────┤
│  Library:  [████░░░░░] A-H: 240 / 1180 scanned (20%) · 940 left            │
│  ⏱ Scanning 2 file(s)…           [████████░░░░] 45/247 (18%)               │
│  ⏱ The Accidental Tourist (1988) [4.2G] — 3m 21s                           │
│  ⏱ Naked (1993)                  [7.3G] — 1m 05s                           │
├────────────────────────────────────────────────────────────────────────────┤
│  Status:[All▾]  Remediation:[Any▾]  Corruption type:[All types▾]            │
│  Search:[____________]  [Hide Skipped]                                      │
├────────────────────────────────────────────────────────────────────────────┤
│  ☐ │ Folder            │ Size │ Status  │ Reason           │ Rem.  │ # │   │
│  ☑ │ 28 Years Later    │11.7G │ CORRUPT │ [Truncated]...   │ NONE  │ 0 │   │
│  ☐ │ Naked (1993)      │ 7.3G │ CORRUPT │ [Bad headers]... │ RESRCH│ 1 │   │
│  ☐ │ Ryans Daughter    │ 2.8G │ CLEAN   │                  │ NONE  │ 0 │   │
├────────────────────────────────────────────────────────────────────────────┤
│  [Scan Activity log — append-only feed, persisted to DB]                   │
├────────────────────────────────────────────────────────────────────────────┤
│  247 total, 47 corrupt, 198 clean, 2 error                                 │
├────────────────────────────────────────────────────────────────────────────┤
│  [Select All][Select None] | [Re-scan TIMEOUTs][Check Re-downloads][Backup] │
│  [Queue for Remediation][Delete + Re-search][Re-search all Group A]         │
│  [Open Folder][Show ffmpeg Log]                                             │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Status vs Remediation — the two columns people confuse

**Two independent axes.** A movie moves along both at the same time.

| Column | Question it answers | Who sets it |
|--------|---------------------|-------------|
| **Status** | *Is this file OK?* (the diagnosis) | the scanner |
| **Remediation** | *What have I done about it?* (the fix pipeline) | you + Radarr |

Examples of combinations:

| Status | Remediation | What it means |
|--------|-------------|---------------|
| CORRUPT | NONE | Broken; not yet acted on |
| CORRUPT | QUEUED | Broken; queued for the next delete + re-search |
| CORRUPT | RESEARCHING | Was broken; deleted, Radarr is re-downloading (row grayed/italic) |
| CLEAN | REMEDIATED | Re-acquired copy verified clean |
| CORRUPT | SKIPPED | Broken, but you chose to leave it alone |
| CLEAN | NONE | Fine; nothing to do |

---

## Status values (scan verdict)

| Status | Color | Meaning | Re-scan changes it? |
|--------|-------|---------|---------------------|
| **CLEAN** | green | Decoded fully, no real errors | No — definitive |
| **CORRUPT** | red (bold) | Genuine decode/demux corruption | No — use Deep Inspect instead |
| **TIMEOUT** | orange | Scan ran out of time (slow NAS / big file) | **Yes** — not a verdict |
| **ERROR** | yellow | Scan couldn't run (ffmpeg/PATH/permissions) | **Yes**, after fixing the cause |
| **EMPTY** | grey | No video file in the folder | Only if you add one |
| **MISSING** | purple | Folder no longer exists on disk | Use "Verify Folder Exists" |
| **SCANNING** | blue | Being decoded right now | — |
| **UNKNOWN** | default | Discovered but never got a real verdict | **Yes** — never decided |

> **Tip:** the **Status** filter has a **Problematic** shortcut (above the dotted
> separator) that shows **TIMEOUT + UNKNOWN + ERROR** together — the three statuses
> where a re-scan can actually change the result. Use this to find everything worth
> re-scanning in one click.

---

## Remediation values (workflow state)

| Remediation | Meaning |
|-------------|---------|
| **NONE** | No action taken (default) |
| **QUEUED** | Marked for the next delete + re-search batch |
| **DELETED** | File removed from disk |
| **RESEARCHING** | Radarr told to find a fresh copy |
| **REMEDIATED** | Confirmed clean replacement acquired |
| **FAILED** | A remediation step errored — needs attention |
| **SKIPPED** | You chose to leave it alone |

Rows in **DELETED / RESEARCHING / REMEDIATED** are grayed out and italicized —
the original has been deleted and a fresh download is in progress.

> Hover the **Status** and **Remediation** column headers in the app for a quick
> reminder tooltip.

---

## View modes (top-left dropdown)

| Mode | What it shows |
|------|---------------|
| **Database (Show All Results)** 💾 | Every previously scanned file from the database. Scan controls are disabled. Use this to review results, queue, and remediate. Default on launch. |
| **Live Scan (Start Fresh)** 🔴 | The table updates in place as a scan runs. Scan controls enabled. Starts automatically when you click Start Scan. |

Both modes read/write the same database.

---

## Scan controls

| Control | What it does |
|---------|--------------|
| **Library: A-H / I-S / T-Z** | Which library roots to scan. |
| **Parallel scans** | How many files to decode at once (1–8). Default 2 (good for 1 GbE NAS). Higher = faster but more CPU/disk. |
| **Timeout/file** | Per-file ffmpeg time budget (30 min … No limit). Raise for large 4K files over a slow NAS. A hit yields TIMEOUT, not CORRUPT. |
| **Start Scan** | Begins scanning. Automatically switches to Live mode. |
| **Stop** | Cancels the running scan (also **Esc**). In-flight files are reset to UNKNOWN — never falsely recorded as CORRUPT. |

### Library coverage bar

Shows overall coverage of the **currently-selected libraries** — folders with a
verdict vs. folders on disk — e.g. `A-H: 240 / 1180 scanned (20%) · 940 left`.
Persists across sessions. The session progress bar below it shows only the current run.

Folders scanned within the last 7 days with a definitive result are **skipped** on
resume (they already have an answer). Use `scan --rescan` to force a full re-scan.

### Per-worker activity panel

One line per concurrently-scanning file — name, size, live elapsed timer. With
`Parallel scans = 2` you see both movies in flight. Lines appear when a file starts
and disappear when it finishes.

---

## Filters (above the table)

| Filter | What it does |
|--------|--------------|
| **Status** | Show one scan verdict, **All**, or **Problematic** (TIMEOUT + UNKNOWN + ERROR). |
| **Remediation** | Show one workflow state, or **Any**. |
| **Corruption type** | Filter CORRUPT rows by triage class: **All types** (default) · **A (re-download)** (truncated/missing-frames — same release is fine) · **B (source damage)** (container/encoder damage — same release won't help) · **Unclassified** (no rule matched). Selecting A or B enables the context batch button. Class is computed on the fly from the ffmpeg error — no extra DB column. |
| **Search** | Filter by folder name as you type. **Ctrl+F** to focus. |
| **Hide Skipped / Show Skipped** | Hides/shows rows whose Remediation is SKIPPED. Applies in any view, scanning or not. |

---

## The table

| Column | Meaning |
|--------|---------|
| **☐** | Row checkbox for bulk selection |
| **Folder** | Movie folder name (full path used for all actions) |
| **Size** | Largest video file size (sorts numerically) |
| **Status** | Scan verdict — see above |
| **Reason** | ffmpeg detail for non-CLEAN files, prefixed with a triage label e.g. `[Incomplete / truncated]`. Hover for the full explanation and whether a re-download will help. |
| **Remediation** | Workflow state — see above |
| **Attempts** | Remediation attempts. **Bold orange at 2**, **bold red at 3+** — repeated failures mean a systemic problem; investigate before trying again. |

Click any column header to sort. CORRUPT rows are bold red. Rows in
DELETED / RESEARCHING / REMEDIATED are grayed and italic.

---

## Right-click menu

The menu is **context-aware** — items appear only when relevant to that row's state.

| Action | Condition | What it does |
|--------|-----------|--------------|
| 📁 **Open Folder** | always | Opens the folder in Explorer |
| 📄 **Show ffmpeg Log** | always | The scan's error output with the triage diagnosis |
| 🔬 **Deep Inspect (ffprobe)** | always | Fast diagnosis: truncated (fixable) vs container damage (bad source) vs ambiguous. See below. |
| 🔁 **Re-scan** | always | Force a fresh decode of the checked rows (or this row if nothing is checked) |
| ➕ **Queue for Remediation** | CORRUPT + NONE | Add to the remediation queue |
| ➖ **Remove from Queue** | QUEUED | Un-queue (back to NONE) |
| 🚫 **Mark as Skipped** | always | Leave this file alone |
| 🔍 **Verify Folder Exists** | always | Re-check disk; marks MISSING if gone |
| 🗑️ **Delete from SQLite Database** | MISSING / FAILED / SKIPPED | Remove a stale record from repair.db only — **never touches files on disk or Radarr** |
| 📋 **Copy Path** | always | Copy the folder path to clipboard |

### Deep Inspect → Full Deep Decode

**Deep Inspect (ffprobe)** runs ffprobe + a header decode + a tail decode (fast,
progress bar). It concludes one of:

| Diagnosis | Offered action |
|-----------|----------------|
| **Truncated / incomplete** — header fine, end damaged | **Delete + Re-search (Radarr)** — re-downloads the same release |
| **Container-level damage (bad source)** — header fails | **Delete + Blocklist + Re-search** — blocklists this release in Radarr and triggers a search for a *different* one |
| **Ambiguous** — header and end both decode clean | **Run Full Deep Decode** |

**Full Deep Decode** (opt-in; decodes the whole file, minutes, cancelable) maps
error locations and returns a verdict:

| Verdict | Meaning | Action |
|---------|---------|--------|
| **CLEAN** | No errors — earlier flag was a false positive | Click **Mark CLEAN in database** in the report (no re-scan needed) |
| **PLAYABLE** | A few localized errors; watchable | Click **Mark as Skipped (keep the file)** in the report, or leave it |
| **RE-DOWNLOAD** | Errors concentrated in one region | Delete + Re-search (same release fine) |
| **BAD SOURCE** | Errors spread across most of the file | Delete + Blocklist + Re-search (need a different release) |

> ffmpeg's `-f null` output can emit "non monotonically increasing dts to muxer"
> warnings on perfectly good files. These are **not** corruption and are ignored by
> both the scanner and the full decode.

---

## Bottom action buttons

The buttons are arranged in two rows. Row 1 is utilities; row 2 is remediation.

### Row 1 — utilities

| Button | What it does |
|--------|--------------|
| **Select All / Select None** | Tick / untick every visible (displayed) row |
| **Re-scan TIMEOUTs** | Force a fresh decode of every TIMEOUT file. Most TIMEOUTs are transient NAS I/O stalls and come back CLEAN. |
| **Check Re-downloads** | Ask Radarr which RESEARCHING movies have finished (Imported), which are Downloading, and which are Pending. Saves opening Radarr. |
| **Backup DB** | Save a timestamped snapshot of the database (also runs automatically on app exit). |
| **Open Folder** | Open the selected row's folder in Explorer |
| **Show ffmpeg Log** | Show the selected row's scan output and triage diagnosis |

### Row 2 — remediation

| Button | What it does |
|--------|--------------|
| **Queue for Remediation** | Mark the checked rows QUEUED. Nothing is deleted yet. |
| **Delete + Re-search** | Run remediation on **checked rows** (or all QUEUED if nothing is checked): delete from disk → Radarr unmonitor → delete file record → monitor → search for the same release. Shows a confirm dialog listing targets before acting. **Group B safety guard:** if any target is a Group B (source-damage) file — where re-downloading the same release won't help — it warns first and offers to blocklist those and search for a *different* release instead. |
| **Re-search all Group A** / **Inspect all Group B** | Context batch button — label and behavior follow the **Corruption type** filter. **Group A selected:** confirm dialog → Delete + Re-search on all visible Group A targets (checked rows preferred, else all shown Group A rows not already in an active remediation state). **Group B selected:** runs Deep Inspect on each visible Group B file sequentially (cancelable progress dialog), then shows a confirm dialog for bad-source targets (blocklist + different-release search), then a confirm dialog for fixable targets (same-release re-search); inconclusive/errors go to a read-only summary dialog. Disabled when Corruption type is *All types* or *Unclassified*, and while a scan or remediation is running. |

> **Group B two-pass note:** the bad-source confirm fires first; if you click Yes
> it starts a worker. The fixable confirm then fires after that worker finishes
> (not simultaneously). If both are present you can also pre-filter by checking
> only the rows you want to handle first.

---

## Scan Activity log

Below the table: an append-only feed of every scan result, **persisted to the
database** so it survives restarts. Format: `HH:MM:SS  <icon>  STATE  Movie Name`,
color-coded by verdict. Newest at the top.

- **Only problems** checkbox — hide CLEAN results, show just CORRUPT/TIMEOUT/ERROR.
- **Clear Log** — wipes the activity feed only; does not touch scan results.
- Click the **Scan Activity** title to collapse/expand the panel.

---

## Right-click vs. bottom buttons — when to use which

| | Right-click menu | Bottom buttons |
|---|---|---|
| **Targets** | The single row you clicked | Checked rows, or a whole DB state |
| **Scope** | Exactly 1 movie | Many movies at once |
| **Menu adapts to row state?** | Yes | No |

**Right-click only** (no button equivalent):
🔬 Deep Inspect · 🚫 Mark as Skipped · ➖ Remove from Queue · 🔍 Verify Folder Exists · 🗑️ Delete from SQLite Database · 📋 Copy Path

**Buttons only** (no menu equivalent):
Delete + Re-search · Re-search all Group A / Inspect all Group B · Re-scan TIMEOUTs · Check Re-downloads · Backup DB · Select All / None

**Both** (different scope):
Open Folder · Show ffmpeg Log · Queue for Remediation · Re-scan

---

## Database backups

The SQLite database holds all scan results, remediation state, the activity log, and
the mtime fingerprints that keep resumed scans fast. It is the one irreplaceable file.

- Backed up automatically **on app exit**.
- Backed up **on demand** via **Backup DB**.
- Destination: `Z:\Repair Media File Deploy\db-backups\` (override with `REPAIR_DB_BACKUP_DIR` in `.env`).
- Newest ~30 copies kept; older ones pruned.
- Uses SQLite's online `.backup` — consistent snapshots, safe while the app is running.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+Q** / **Ctrl+W** | Quit |
| **Esc** | Stop scan |
| **Ctrl+R** | Refresh table |
| **Ctrl+F** | Focus the search box |

---

*See [MANUAL.md](MANUAL.md) for scenarios, CLI reference, and troubleshooting.*
*See [WORKFLOW_CHECKLIST.md](WORKFLOW_CHECKLIST.md) for step-by-step task checklists.*
