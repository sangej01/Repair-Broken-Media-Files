# Workflow Checklist

Open this, pick your task, follow the steps.

---

## How to read a checklist

| Symbol | Meaning |
|--------|---------|
| `- [ ]` | A step you click or do in the app. Check it off as you go. |
| _Italics_ | A confirmation — what you should see after the previous step. |
| **IF … THEN …** | A branch — read the condition, follow the matching path. |

---

## Table of Contents

**GETTING STARTED**
- [1. First launch / sanity check](#1-first-launch--sanity-check)

**SCANNING**
- [2. Run a full library scan (first time / resume)](#2-run-a-full-library-scan-first-time--resume)
- [3. Re-scan the TIMEOUTs](#3-re-scan-the-timeouts)
- [4. Re-scan specific files](#4-re-scan-specific-files)

**FIXING CORRUPT FILES**
- [5. Fix corrupt movies end-to-end](#5-fix-corrupt-movies-end-to-end)

**TRACKING & VERIFYING**
- [6. Check re-downloads & verify the new copy is CLEAN](#6-check-re-downloads--verify-the-new-copy-is-clean)

**HOUSEKEEPING**
- [7. Skip a file permanently](#7-skip-a-file-permanently)
- [8. Delete a stale / MISSING record from the database](#8-delete-a-stale--missing-record-from-the-database)
- [9. Back up & restore the database](#9-back-up--restore-the-database)

---

## GETTING STARTED

---

## 1. First launch / sanity check

**When to use this:** The very first time you open the app, or after a fresh install, to confirm it connected to Radarr and loaded your library.

_(checklist to be filled in — see operation 5 for the format)_

---

## SCANNING

---

## 2. Run a full library scan (first time / resume)

**When to use this:** You want to scan (or continue scanning) your entire Radarr library for corruption.

_(checklist to be filled in — see operation 5 for the format)_

---

## 3. Re-scan the TIMEOUTs

**When to use this:** Some movies timed out during the last scan and came back with no verdict — you want to try them again.

_(checklist to be filled in — see operation 5 for the format)_

---

## 4. Re-scan specific files

**When to use this:** You want to re-scan one or a handful of specific movies, not the whole library.

_(checklist to be filled in — see operation 5 for the format)_

---

## FIXING CORRUPT FILES

---

## 5. Fix corrupt movies end-to-end

**When to use this:** You have one or more movies with **Status = CORRUPT** and you want to identify, triage, and fix them.

**Before you start:** No other scan should be running (check the status bar). Deep Inspect and batch inspect are read-only and safe during a scan, but do not start a full re-scan while one is already running.

---

### 5.0 Setup (once per session)

- [ ] Set the **View** dropdown to **Database (Show All Results)**.
- [ ] Set the **Status** filter to **CORRUPT**.
- [ ] Set the **Remediation** filter to **Any**.
- [ ] Clear the **Search** box (leave it blank).
- [ ] Decide whether to show or hide skipped movies (toggle **Hide Skipped** to your preference).

_You should see: only CORRUPT rows in the table._

---

### 5.1 Decide: Group A or Group B?

The **Reason** column tells you which group each movie belongs to. You can read it yourself, or just use the **Corruption type** filter and let the app group them for you (recommended for batches).

**Reading the Reason label by eye:**

| If the Reason starts with… | Group | Action |
|----------------------------|-------|--------|
| `[Incomplete / truncated]` | **A** | Re-download (same release) |
| `[Missing reference frames]` | **A** | Re-download (same release) |
| `[No decodable frames]` | **A** | Re-download (same release) |
| `[Partial corruption (concealed)]` | **A** | Re-download (same release) |
| `[Generic corruption]` | **A** | Re-download (same release) |
| `[Broken container (MKV)]` | **B** | Inspect first; may blocklist |
| `[Encoder artifact (H.264/H.265)]` | **B** | Inspect first; may blocklist |
| `[Encoder artifact (slice decode)]` | **B** | Inspect first; may blocklist |
| `[Malformed packet]` | **B** | Inspect first; may blocklist |
| `[Timestamp (DTS/PTS) problem]` | **B** | Inspect first; may blocklist |

> **Tip:** If you have a mix, handle Group A first (it's faster), then come back for Group B.

---

### 5.2 Group A — batch re-download

- [ ] Set the **Corruption type** filter to **A (re-download)**.
  _You should see: only Group A CORRUPT rows._
- [ ] (Optional) Check the checkboxes for specific rows if you only want to act on some of them. If nothing is checked, the batch acts on all visible rows.
- [ ] Click **Re-search all Group A**.
- [ ] Review the confirm dialog — it lists the movies that will be deleted + re-searched via Radarr.
- [ ] Click **Yes**.

_Done when: those rows show **RESEARCHING** (grayed / italic). Radarr will grab a new copy of the same release._

---

### 5.3 Group B — batch inspect + auto-fix (with blocklist)

- [ ] Set the **Corruption type** filter to **B (source damage)**.
  _You should see: only Group B CORRUPT rows._
- [ ] (Optional) Check the checkboxes for specific rows. If nothing is checked, the batch acts on all visible Group B rows.
- [ ] Click **Inspect all Group B**.
  _A cancelable progress dialog appears. The app runs a deep ffprobe + header/tail decode on each file one at a time. This may take several minutes for large files._

When the inspection finishes, the app automatically handles definitive results and shows confirms:

**Step A — Bad-source confirm (appears FIRST):**
- [ ] Read the dialog — it lists movies the app determined are **unfixable** (bad at the source). Confirming will blocklist the current release in Radarr and trigger a search for a **different** release.
  - **IF** you want to blocklist these and search for a different release → click **Yes**.
  - **IF** you are unsure → click **No** (those rows are left unchanged; you can handle them individually via right-click → Deep Inspect).

**Step B — Fixable confirm (appears SECOND):**
- [ ] Read the dialog — it lists movies the app determined are **fixable** (a re-download of the same release should work). Confirming will delete + re-search via Radarr.
  - **IF** you want to re-search these → click **Yes**.
  - **IF** you want to skip for now → click **No**.

**Inconclusive / errors:**
- [ ] After the confirms, a read-only **summary dialog** appears listing any movies whose result was inconclusive or errored. No automatic action is taken for these. Note them down and handle them individually via right-click → **Deep Inspect**.

**GOTCHA — concurrent remediation block:**

> **IF** the bad-source group and the fixable group are both non-empty, clicking **Yes** to the bad-source confirm starts a remediation. The fixable confirm then fires immediately, but the app will refuse to start a second concurrent remediation and will show "A remediation is already in progress."
>
> **Workaround (two-pass):**
> - Click **Yes** to the bad-source confirm; click **No** (or dismiss) the fixable confirm.
> - Wait for the bad-source rows to move to **RESEARCHING**.
> - Click **Inspect all Group B** again — now only the fixable rows remain; click **Yes** to that confirm.
>
> **Alternative (pre-filter):** Before clicking **Inspect all Group B**, check only the rows you want to handle in this pass (e.g. only the fixable ones based on the Reason label), so only one confirm type fires.

**Blocklist caveat:**

> **IF** a movie was manually imported (no Radarr "grabbed" history record), the blocklist step cannot find a history entry to block. The app falls back to a plain re-search for the same release and logs a warning. If this happens, you may need to blocklist the release manually in Radarr.

_Done when: bad-source + fixable rows show **RESEARCHING**; inconclusive/error movies noted for individual follow-up._

---

### 5.4 Single file (when you only have one, or want to look before acting)

- [ ] Right-click the movie row → **Deep Inspect**.
  _A progress dialog runs an ffprobe + header/tail decode. Results appear in a report dialog._
- [ ] Read the report.
  - **IF** the report offers **Delete + Re-search** → click it (Group A path; re-downloads the same release).
  - **IF** the report offers **Delete + Blocklist + Re-search** → click it (Group B bad-source path; blocklists the bad release and searches for a different one).
  - **IF** the result is ambiguous and the report offers **Run Full Deep Decode** → click it for a whole-file error map, then decide.
  - **IF** the result is inconclusive → note the movie; re-check after Radarr grabs a new release, or investigate manually.

_Done when: the row moves to **RESEARCHING** (if you accepted an action)._

---

### 5.5 Verify the fix

These steps happen **after Radarr has imported a new copy** (check Radarr's activity feed or wait for the row to update).

- [ ] Click **Check Re-downloads** (bottom toolbar).
  _The app queries Radarr for recently imported movies and updates the table._
- [ ] Look for rows showing **Imported** in the Remediation column.
  - **IF** there are Imported rows → right-click each one → **Re-scan** to verify the new file.
    _The row status will flip to **CLEAN** if the new copy is good, or back to **CORRUPT** if it is still bad._
- [ ] Review the results:
  - **IF** a re-scanned movie comes back **CLEAN** → done for that movie.
  - **IF** a re-scanned movie comes back **CORRUPT** again → the re-downloaded copy is also bad (bad source). Use the Group B / blocklist path (section 5.3 or 5.4) to blocklist this release and let Radarr grab a **different** one.
  - **IF** a movie's **Attempts** column shows 3 or more → stop re-searching; investigate manually (the problem may not be fixable by re-downloading).

> **Note:** Radarr showing "Imported" does **not** mean the new file is clean. Always re-scan the imported movie to get a CLEAN verdict from the scanner.

_Done when: fixed movies show **CLEAN** (or **REMEDIATED** if they were manually resolved)._

---

## TRACKING & VERIFYING

---

## 6. Check re-downloads & verify the new copy is CLEAN

**When to use this:** You triggered re-searches earlier and want to check whether Radarr has imported new copies, then verify they are actually clean.

_(checklist to be filled in — see operation 5 for the format)_

---

## HOUSEKEEPING

---

## 7. Skip a file permanently

**When to use this:** A movie is CORRUPT but you don't want the app to keep flagging it (e.g. you know the source is bad and there is no better release available).

_(checklist to be filled in — see operation 5 for the format)_

---

## 8. Delete a stale / MISSING record from the database

**When to use this:** A row shows MISSING or FAILED and the movie no longer exists in your library; you want to remove the record entirely.

_(checklist to be filled in — see operation 5 for the format)_

---

## 9. Back up & restore the database

**When to use this:** Before a major action (batch blocklist, bulk delete), or to recover from a bad state.

_(checklist to be filled in — see operation 5 for the format)_

---

## See also

- **[INTERFACE.md](INTERFACE.md)** — every control explained (buttons, filters, columns, menus).
- **[MANUAL.md](MANUAL.md)** — how and why the app works the way it does (architecture, scan logic, Radarr integration).
