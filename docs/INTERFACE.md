# Interface Guide: Repair Broken Media Files

A tour of the application window — what every control does. For step-by-step
tasks see [WORKFLOW.md](WORKFLOW.md); for full reference and CLI see
[USERGUIDE.md](USERGUIDE.md).

---

## The window at a glance

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Repair Broken Media Files                                                   │
│  Scan your movie library for structurally broken files and remediate them    │
├────────────────────────────────────────────────────────────────────────────┤
│  View: [Database (Show All Results) ▾]              💾 info message here     │
├────────────────────────────────────────────────────────────────────────────┤
│  Library: ☑ A-H ☑ I-S ☑ T-Z   Parallel scans:[2▾]  Timeout/file:[30 min▾]   │
│                                              [Start Scan]  [Stop]             │
├────────────────────────────────────────────────────────────────────────────┤
│  Last scan: ...              [███████░░░░░░░] 45/247 (18%)                    │
├────────────────────────────────────────────────────────────────────────────┤
│  Status:[All▾]  Remediation:[Any▾]  Search:[__________]  [Hide Skipped]      │
├────────────────────────────────────────────────────────────────────────────┤
│  ☐ │ Folder            │ Size │ Status  │ Reason           │ Remediation │ # │
│  ☑ │ 28 Years Later    │11.7G │ CORRUPT │ [Truncated] ...  │ NONE        │ 0 │
│  ☐ │ Naked (1993)      │ 7.3G │ CORRUPT │ [Bad headers]... │ RESEARCHING │ 1 │
│  ☐ │ Ryans Daughter    │ 2.8G │ CLEAN   │                  │ NONE        │ 0 │
├────────────────────────────────────────────────────────────────────────────┤
│  247 total, 47 corrupt, 198 clean, 2 error                                   │
├────────────────────────────────────────────────────────────────────────────┤
│  [Select All][Select None]      [Queue for Remediation][Delete + Re-search]  │
│                                 [Open Folder][Show ffmpeg Log]               │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Status vs Remediation — the two columns people confuse

These track **two different things** and are independent of each other:

- **Status** = *what the scan found* — the file's condition. Set by the scanner.
  Answers **"Is this file OK?"**
- **Remediation** = *what you have done about it* — the fix-it workflow. Set by you
  and the Radarr flow. Answers **"Where is this in the fix pipeline?"**

Think of it as **diagnosis (Status)** vs **treatment plan (Remediation)**.

A single file moves along both axes independently. For example:

| Status | Remediation | What it means |
|--------|-------------|---------------|
| CORRUPT | NONE | Broken; you haven't acted yet (typical starting point) |
| CORRUPT | QUEUED | Broken; you've marked it for the next delete + re-search batch |
| CORRUPT | RESEARCHING | Was broken; deleted and Radarr is re-downloading (row shown grayed/italic) |
| CLEAN | REMEDIATED | Re-acquired copy verified clean — success |
| CORRUPT | SKIPPED | Broken, but you chose to leave it alone |
| CLEAN | NONE | Fine; nothing to do |

### Status values (scan verdict)

| Status | Color | Meaning | Re-scan changes it? |
|--------|-------|---------|---------------------|
| **CLEAN** | green | Decoded fully, no real errors | No — definitive |
| **CORRUPT** | red (bold) | Genuine decode/demux corruption | No — deterministic (use Deep Inspect) |
| **TIMEOUT** | orange | Scan ran out of time (slow NAS / big file) | **Yes** — not a verdict |
| **ERROR** | yellow | Scan couldn't run (ffmpeg/PATH/permission) | **Yes**, after fixing the cause |
| **EMPTY** | grey | No video file in the folder | Only if you add one |
| **MISSING** | purple | Folder no longer exists on disk | Use "Verify Folder Exists" |
| **SCANNING** | blue | Being decoded right now | — |
| **UNKNOWN** | default | Discovered but never got a real verdict | **Yes** — never decided |

### Remediation values (workflow state)

| Remediation | Meaning |
|-------------|---------|
| **NONE** | No action taken (default) |
| **QUEUED** | You marked it for the delete + re-search batch |
| **DELETED** | File removed from disk |
| **RESEARCHING** | Radarr told to find a fresh copy |
| **REMEDIATED** | Confirmed clean replacement acquired |
| **FAILED** | A remediation step errored — needs your attention |
| **SKIPPED** | You chose to leave it alone |

> Tip: the **Status** and **Remediation** column headers (and the matching filter
> labels) have tooltips that summarize all of the above — hover them in the app.

---

## View modes (top-left dropdown)

| Mode | What it shows |
|------|---------------|
| **Database (Show All Results)** 💾 | Every previously scanned file from the database. Scan controls are disabled (view/manage only). Use this to review results, queue, and remediate. |
| **Live Scan (Start Fresh)** 🔴 | The table for the selected libraries, updating in place as a scan runs. Scan controls enabled. |

Both modes read/write the same database — Live mode saves everything, Database mode
displays it. Starting a scan automatically switches you to Live mode.

---

## Scan controls

| Control | What it does |
|---------|--------------|
| **Library: A-H / I-S / T-Z** | Which library roots to scan (checkboxes). |
| **Parallel scans** | How many files to decode at once (1–8). Higher = faster but more CPU/disk. Default 2 (good for 1 GbE NAS). |
| **Timeout/file** | Per-file ffmpeg time budget (30 min … No limit). Raise it for large 4K files over a slow network. A hit here yields TIMEOUT (not CORRUPT). |
| **Start Scan** | Begins scanning. Switches to Live mode and pre-loads the selected libraries so you see rows immediately. |
| **Stop** | Cancels the running scan (also **Esc**). |

Folders scanned within the last 7 days with a definitive result are **skipped** on a
resumed scan (they already have an answer). Use the CLI `scan --rescan` to force a
full re-scan, or `rescan-corrupt` to re-check only flagged folders.

---

## Filters (above the table)

| Filter | What it does |
|--------|--------------|
| **Status** | Show one scan verdict, **All**, or **Problematic**. |
| **Problematic** (in the Status list) | Shortcut for **TIMEOUT + UNKNOWN + ERROR** at once — every file worth re-scanning. Sits above a dotted separator. |
| **Remediation** | Show one workflow state, or **Any**. |
| **Search** | Filter by folder name as you type (**Ctrl+F** to focus). |
| **Hide Skipped / Show Skipped** | Toggle. Hides folders not being worked this scan so you can focus on live activity. Mid-scan it hides rows that already have a definitive result; after the scan it hides anything not scanned this run. Click again to show them. |

---

## The table

| Column | Meaning |
|--------|---------|
| **☐** | Row checkbox for bulk selection |
| **Folder** | Movie folder name (full path stored for actions) |
| **Size** | Largest video file size (sorts numerically) |
| **Status** | Scan verdict — see the table above |
| **Reason** | ffmpeg detail for non-CLEAN files, prefixed with a triage label (e.g. `[Incomplete / truncated]`). Hover for the full explanation + whether a re-download will help. |
| **Remediation** | Workflow state — see the table above |
| **Attempts** | Remediation attempts. **Bold orange at 2**, **bold red at 3+** — repeated attempts mean a systemic problem; investigate before trying again. |

Click any column header to sort. **Row styling:**
- **CORRUPT** rows are bold red for visibility.
- Rows in **DELETED / RESEARCHING / REMEDIATED** are **grayed out and italicized** —
  the original has been deleted and a fresh download requested.

---

## Right-click a row

| Action | What it does |
|--------|--------------|
| 📁 **Open Folder** | Opens the folder in Explorer |
| 📄 **Show ffmpeg Log** | The scan's error output, with the triage diagnosis |
| 🔬 **Deep Inspect (ffprobe)** | Fast diagnosis: truncated (fixable) vs container damage (bad source) vs ambiguous. See below. |
| ➕ **Queue for Remediation** | Add a CORRUPT file to the queue |
| ➖ **Remove from Queue** | Un-queue (back to NONE) |
| 🚫 **Mark as Skipped** | Leave this file alone |
| 🔍 **Verify Folder Exists** | Re-check disk; mark MISSING if gone |
| 🗑️ **Delete from SQLite Database** | Remove a stale record (repair.db only — never touches disk or Radarr) |
| 📋 **Copy Path** | Copy the folder path |

### Diagnosing: Deep Inspect → Full Deep Decode

- **Deep Inspect (ffprobe)** runs ffprobe + a header decode + a tail decode
  (progress bar, fast). It concludes:
  - **Truncated / incomplete** → offers a one-click **Delete + Re-search (Radarr)**.
  - **Container-level damage** → the source/release is bad; a re-download of the
    *same* release likely won't help.
  - **Ambiguous** (header + end both clean) → offers **Run Full Deep Decode**.
- **Full Deep Decode** (opt-in; decodes the whole file, minutes long, cancelable)
  maps where errors occur and returns a verdict: **CLEAN / PLAYABLE / RE-DOWNLOAD /
  BAD SOURCE**. RE-DOWNLOAD offers the one-click fix; BAD SOURCE means find a
  different release.

> Benign note: ffmpeg's `-f null` output can emit "non monotonically increasing dts
> to muxer" warnings on perfectly good files. These are **not** corruption and are
> ignored by both the scanner and the full decode.

---

## Bottom action buttons

| Button | What it does |
|--------|--------------|
| **Select All / Select None** | Toggle the checkboxes on visible rows |
| **Queue for Remediation** | Mark the checked rows QUEUED |
| **Delete + Re-search** | Run remediation on all QUEUED files (delete from disk → Radarr unmonitor → delete record → monitor → search). Asks to confirm. |
| **Open Folder** | Open the selected row's folder |
| **Show ffmpeg Log** | Show the selected row's scan output |

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+Q** / **Ctrl+W** | Quit |
| **Esc** | Stop scan |
| **Ctrl+R** | Refresh table |
| **Ctrl+F** | Focus the search box |

---

*Part of the Media Tools Consortium. See [USERGUIDE.md](USERGUIDE.md) and
[WORKFLOW.md](WORKFLOW.md) for more.*
