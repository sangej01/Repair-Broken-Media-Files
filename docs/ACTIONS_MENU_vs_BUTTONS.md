# Right-Click Menu vs. Bottom Buttons

Two ways to act on movies. They are not "single vs. group" — it's **scope**
(one row vs. many) crossed with **how the target is chosen** (the row your
cursor is on, the checkboxes, or a saved state in the database).

---

## The one-line rule

- **Right-click a row** = do this to **THIS one movie** (the row you clicked).
  It is also the *only* place for the precise, per-movie actions.
- **Bottom buttons** = do this to a **batch** — the **checked** rows, or every
  file in a given state (e.g. all QUEUED, all TIMEOUT).

---

## Side-by-side

| | Right-click menu | Bottom buttons |
|---|---|---|
| **What it targets** | The single row you right-clicked | The checked rows, or a whole DB state |
| **Typical scope** | Exactly 1 movie | Many movies at once |
| **How it decides** | Where your cursor is (current row) | Checkboxes / a state query |
| **Menu adapts to the row?** | Yes — only shows relevant items | No — always the same buttons |

---

## Only in the RIGHT-CLICK menu (per-movie actions)

These have **no button**. They act on one movie by nature:

- **🔬 Deep Inspect (ffprobe)** — diagnose how bad one file is
- **🚫 Mark as Skipped** — "leave this one alone"
- **➖ Remove from Queue** — un-queue this one (only shows if it's QUEUED)
- **🔍 Verify Folder Exists** — is this folder still on disk?
- **🗑️ Delete from SQLite Database** — forget this row *(only shows for
  MISSING / FAILED / SKIPPED)*. **Does NOT delete the movie file.**
- **📋 Copy Path**

> The menu is **context-aware**: "Queue for Remediation" only appears when the
> row is CORRUPT + NONE; "Delete from SQLite Database" only for
> MISSING/FAILED/SKIPPED. If you don't see an item, it doesn't apply to that row.

---

## Only in the BOTTOM BUTTONS (batch actions)

These have **no menu item**. They act on a group:

- **Delete + Re-search** — ⚠️ the destructive one. Acts on the **checked** rows;
  if nothing is checked, falls back to every **QUEUED** file. Always lists what
  it will delete and asks first.
- **Re-scan TIMEOUTs** — re-check every TIMEOUT file at once
- **Check Re-downloads** — ask Radarr about all RESEARCHING files
- **Backup DB** — snapshot the whole database
- **Select All / Select None** — tick / untick every visible row

---

## Available in BOTH (same action, different scope)

| Action | Right-click (1 movie) | Button (batch) |
|--------|-----------------------|----------------|
| Open Folder | this row | current row |
| Show ffmpeg Log | this row | current row |
| Queue for Remediation | this row | all **checked** rows |
| Re-scan | checked rows if any, else this row | Re-scan **TIMEOUTs** (by state) |

> Note: the menu's **🔁 Re-scan (selected / this file)** uses your **checked**
> rows if any are ticked, and only falls back to the clicked row if nothing is
> checked. So even that menu item can act on a batch.

---

## When to use which

- **Fixing one specific movie** → right-click it. You get Deep Inspect, Skip,
  Verify, Copy Path — the precise tools.
- **Fixing many at once** → tick their checkboxes, then use the bottom buttons
  (Queue, then Delete + Re-search).
- **Cleaning up a whole category** → buttons by state: Re-scan TIMEOUTs, or
  Delete + Re-search the QUEUED set.

---

## Golden Retriever summary

- 👉 **Right-click** = "this movie, right here." Scalpel.
- 🔲 **Checkboxes + buttons** = "all of these." Batch.
- 🔬 Deep Inspect, 🚫 Skip, 🔍 Verify, 🗑️ Delete-record, 📋 Copy Path = **right-click only.**
- 🗑️ Delete + Re-search, 🔁 Re-scan TIMEOUTs, ✅ Check Re-downloads, 💾 Backup = **buttons only.**
