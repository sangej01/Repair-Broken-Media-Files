# IDIOT'S GUIDE: Fixing Bad Movie Files

You found a bad movie. This tells you what to do. Follow the steps. Don't think too hard.

**Feeling lost in the moving parts? Start with the [CHEATSHEET.md](CHEATSHEET.md) — the whole app on one page.**

Prefer a task-by-task checklist? See **[WORKFLOW_CHECKLIST.md](WORKFLOW_CHECKLIST.md)**.

---

## The 10-second version

1. Look at the **Status** column.
2. If it says **CORRUPT**, look at the **Reason** column.
3. If the Reason starts with **`[Incomplete / truncated]`** → tick the box → click **Delete + Re-search**. Done.
   - *Have a lot of them?* Set **Corruption type** filter → **A (re-download)** → click **Re-search all Group A**. Done in one shot.
4. If the Reason starts with **`[Broken container]`** or **`[Encoder artifact]`** → right-click the row → **Deep Inspect** first, then do what it tells you.
   - *Have several of them?* Set **Corruption type** filter → **B (source damage)** → click **Inspect all Group B**. The app inspects them all and automatically kicks off the right fix for each one.
5. Everything else: see the table below.

That's it. The rest of this file is just more detail for when you forget.

---

## What the words mean (Status column)

| Word | What it means | Baby version |
|------|---------------|--------------|
| **CLEAN** | File is fine. | Good boy. Leave it. |
| **CORRUPT** | File is broken. | Bad. Needs fixing. |
| **TIMEOUT** | Scan took too long, no verdict. | Probably fine. Just slow. Re-scan it. |
| **EMPTY** | No video file in the folder. | Nothing there to check. |
| **MISSING** | The folder is gone from the disk. | It moved or got deleted. |
| **ERROR** | Scanner couldn't run. | Something went wrong. Re-scan. |
| **SCANNING** | Being checked right now. | Wait. |
| **UNKNOWN** | Never got a real answer. | Re-scan it. |

**Only CORRUPT files are actually broken.** TIMEOUT / ERROR / UNKNOWN just mean "try again."

---

## What the words mean (Remediation column)

This column is **what YOU have done about it so far.**

| Word | Meaning |
|------|---------|
| **NONE** | You haven't touched it. |
| **QUEUED** | You lined it up to be fixed (but haven't fixed it yet). |
| **DELETED** | The old file was deleted. |
| **RESEARCHING** | Old file gone, Radarr is downloading a new copy. |
| **REMEDIATED** | Fully handled. |
| **SKIPPED** | You told it "leave this one alone." |
| **FAILED** | The fix didn't work (often: movie not in Radarr). |

---

## THE MAIN JOB: Fixing CORRUPT files

Look at the **Reason** column. The first thing in `[square brackets]` tells you everything.

### GROUP A — Just re-download it. Don't overthink.

If the Reason says any of these:

- `[Incomplete / truncated]`  ← **this is the most common one**
- `[Missing reference frames]`
- `[No decodable frames]`
- `[Partial corruption (concealed)]`
- `[Generic corruption]`

**These are download accidents. A fresh copy fixes them.**

**Do this (one-by-one):**
1. Tick the checkbox on the left of the row (or several rows).
2. Click **Delete + Re-search** (the red button at the bottom).
3. Say **Yes**.
4. Walk away. Radarr grabs a new copy.
5. Later, click **Check Re-downloads** to see which ones arrived.
6. When one says "Imported," **re-scan it** to make sure the new copy is actually good.

**Do this (whole-class batch — faster when you have many):**
1. Set the **Corruption type** filter (next to Remediation in the filter row) to **A (re-download)**.
2. The table narrows to only Group A CORRUPT files.
3. Click **Re-search all Group A** (the button that appeared / became active at the bottom).
4. Confirm the list and say **Yes**. Radarr handles the rest.

> **Do NOT Deep Inspect these.** You already know the answer. Deep Inspect just wastes time telling you what the Reason already said.

---

### GROUP B — Check first. The source might just be bad.

If the Reason says any of these:

- `[Broken container (MKV)]`
- `[Encoder artifact (H.264/H.265)]`
- `[Encoder artifact (slice decode)]`
- `[Malformed packet]`
- `[Timestamp (DTS/PTS) problem]`

**These usually mean the movie file itself was made wrong. Re-downloading the SAME copy will probably give you the SAME broken movie.** So check before you waste a download.

**Do this (one at a time):**
1. **Right-click** the row → **🔬 Deep Inspect (ffprobe)**.
2. Wait for the little box to finish.
3. Read the **VERDICT** line at the bottom of the report:

| VERDICT says | What to do |
|--------------|------------|
| **RE-DOWNLOAD** | Damage is in one spot. A new copy will likely fix it. → **Delete + Re-search.** |
| **BAD SOURCE** | The whole file is rotten. A new copy of the SAME release won't help. → **Delete + Blocklist + Re-search** (app tells Radarr to find a *different* release). |
| **PLAYABLE** | Tiny glitch, movie still watches fine. → click **Mark as Skipped (keep the file)** in the report, or just leave it. |
| **CLEAN** | Turns out it's actually fine. → click **Mark CLEAN in database** in the report (or **Re-scan** to clear the CORRUPT flag). |

**Do this (whole-class batch — when you have several Group B files):**
1. Set the **Corruption type** filter to **B (source damage)**.
2. Click **Inspect all Group B** at the bottom.
3. Watch the progress dialog as each file is inspected (ffprobe, header + tail).
4. When it finishes, the app **automatically acts on definitive results**:
   - **Fixable** → confirmation dialog for Delete + Re-search (same release is fine).
   - **Bad source** → confirmation dialog for Delete + Blocklist + search for a *different* release. Radarr will not grab the same bad release again.
   - **Inconclusive / errors** → summary dialog showing what needs your attention.
5. If all results were definitive, no summary dialog appears — it's already handled.

---

## The buttons (bottom of the window)

| Button | What it does |
|--------|--------------|
| **Select All** / **Select None** | Tick / untick every visible row. |
| **Re-scan TIMEOUTs** | Re-checks every slow file. Do this often — most come back CLEAN. |
| **Backup DB** | Saves a copy of your results. (Also happens automatically when you close.) |
| **Check Re-downloads** | Asks Radarr which new copies have arrived. |
| **Queue for Remediation** | Marks ticked files to fix later (doesn't fix them yet). |
| **Delete + Re-search** | ⚠️ **The big one.** Deletes the bad file and tells Radarr to get a new one. Acts on **checked** rows; if nothing is checked, falls back to the **QUEUED** files. It lists what it will delete and asks first. |
| **Re-search all Group A** / **Inspect all Group B** | Context batch button — label and action change based on the **Corruption type** filter. Set filter to *A* → runs Delete + Re-search on all visible Group A targets. Set to *B* → runs Deep Inspect on all visible Group B targets; fixable ones get Delete + Re-search, bad-source ones get Delete + Blocklist + different-release search, inconclusive ones get a summary. Disabled unless A or B is selected. |
| **Open Folder** | Opens the movie's folder in Windows. |
| **Show ffmpeg Log** | Shows the full error + a plain-English diagnosis. |

---

## Right-click a row for more options

- **🔬 Deep Inspect (ffprobe)** — check how bad it really is (Group B).
- **🔁 Re-scan** — check this one file again.
- **➕ Queue for Remediation** / **➖ Remove from Queue**
- **🚫 Mark as Skipped** — "leave this one alone forever."
- **🔍 Verify Folder Exists** — did someone delete/move the folder?
- **🗑️ Delete from SQLite Database** — removes the ROW ONLY. **Does NOT touch your movie file.** Just makes the app forget it.
- **📋 Copy Path**

---

## Right-click vs. Buttons (which do I use?)

Dead simple:

- 👉 **Right-click a row** = do it to **THIS one movie** (the row you clicked).
- 🔲 **Checkboxes + bottom buttons** = do it to **a whole batch** (all checked, or a whole group like all TIMEOUTs).

Some things live in **only one** place:

- **Right-click only:** 🔬 Deep Inspect, 🚫 Mark as Skipped, 🔍 Verify Folder Exists, 🗑️ Delete from SQLite Database, 📋 Copy Path.
- **Buttons only:** 🗑️ Delete + Re-search, 🔎 Re-search all Group A / Inspect all Group B, 🔁 Re-scan TIMEOUTs, ✅ Check Re-downloads, 💾 Backup DB.

> Want the full breakdown (every action, both places, when to use which)?
> See the **Right-click vs. bottom buttons** section in **[INTERFACE.md](INTERFACE.md)**.

---

## Common "what do I do" moments

**"There's a scary red CORRUPT file."**
→ Read the Reason. Group A = Delete + Re-search. Group B = Deep Inspect first.

**"Lots of TIMEOUTs."**
→ Click **Re-scan TIMEOUTs**. Most turn CLEAN. They were just slow, not broken.

**"I deleted + re-searched. Now what?"**
→ Wait a while. Click **Check Re-downloads**. When it says "Imported," **re-scan that movie** to confirm the new copy is good. (Radarr "Imported" only means a file arrived — NOT that it's good.)

**"A file keeps coming back CORRUPT after re-downloading."**
→ The **Attempts** number is going up (turns orange at 2, red at 3+). The source release is bad. Right-click → **Deep Inspect** to confirm it's a bad source, then use **Delete + Re-search** from the report dialog — or set Corruption type to **B** and use **Inspect all Group B**, which will automatically blocklist the bad release and tell Radarr to find a *different* one. If nothing else is available, **Mark as Skipped**.

**"Deleted + Re-search said FAILED — 'Movie not found in Radarr.'"**
→ Radarr doesn't have this movie in its library, so it can't fetch a new copy. Add it to Radarr yourself, or handle the file manually.

**"The table is blank."**
→ Set the **View** dropdown (top-left) to **Database (Show All Results)**. Set Status = **All**, Remediation = **Any**, and clear the **Search** box.

---

## The one rule you must not break

**Do NOT run a second scan (or a command-line scan) while this app is open and scanning.**
Two scanners writing at once can crash the app and mess up the results. One scanner at a time. Always.

---

## Golden Retriever summary

- 🟢 **CLEAN** = good boy, leave it.
- 🔴 **CORRUPT** + `[truncated]` = tick box, press **Delete + Re-search** — *or* set Corruption type to **A**, press **Re-search all Group A** to do the whole class at once.
- 🔴 **CORRUPT** + `[container/encoder]` = right-click, **Deep Inspect**, then obey the VERDICT — *or* set Corruption type to **B**, press **Inspect all Group B**; fixable ones re-download, bad-source ones get blocklisted + different release, all automatically.
- 🟠 **TIMEOUT** = press **Re-scan TIMEOUTs**, it's probably fine.
- After any re-download: **Check Re-downloads**, then **re-scan** the new copy.

Good boy. 🦴
