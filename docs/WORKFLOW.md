# Workflow Guide: Repair Broken Media Files

A practical, task-oriented guide to using this tool. For detailed feature reference, see [USERGUIDE.md](USERGUIDE.md).

---

## Primary Workflow: Delete Corrupt Movies and Tell Radarr to Re-acquire

This is the main reason this tool exists. Here's exactly how to do it.

### Step 1: Open in Database View

When you launch the app, it starts in **"Database (Show All Results)"** mode by default.

```powershell
cd "Repair Broken Media Files"
python main.py
```

You'll see all your previously scanned movies in the table. The header dropdown shows:
> **View:** Database (Show All Results) ▾

### Step 2: Filter to Find Corrupt Movies

Use the filter controls to narrow down what you're looking at:

- **Filter dropdown:** Change "All" → **"CORRUPT"**
- Now only corrupt files appear
- Click **Size** column header to sort by file size (biggest waste of space first)
- Or click **Folder** to sort alphabetically

**Tip:** Check the status bar at the bottom to see counts:
> *247 total, 47 corrupt, 198 clean, 2 error*

### Step 3: Select Movies to Remediate

You have three options for queueing movies:

#### Option A: Bulk Selection (Multiple Movies)

1. Check the boxes (☑) next to corrupt movies you want to fix
2. Or click **"Select All"** to select all visible (filtered) movies
3. Click **"Queue for Remediation"** button at the bottom
4. State column changes from `NONE` to `QUEUED`

#### Option B: Quick Queue (Single Movie)

1. **Right-click** on a corrupt movie row
2. Select **"➕ Queue for Remediation"** from the context menu
3. State changes immediately

#### Option C: CLI (For Power Users)

```powershell
# Queue all corrupt files at once
python main.py queue --all-corrupt

# Queue specific movies by name (partial match)
python main.py queue "28 Years Later"
python main.py queue "Naked"
```

### Step 4: Verify Queue Before Action

Before deleting anything, double-check what's queued:

- Switch **Remediation** dropdown to **"QUEUED"**
- Now you only see queued movies
- Review the list one more time
- Right-click → **"Show ffmpeg Log"** to see WHY each is marked corrupt

**Safety check:** If something looks wrong (e.g., a file you think might actually be fine), right-click → **"Mark as Skipped"** to remove it from the queue.

### Step 5: Execute the Delete + Re-search

1. Click the red **"Delete + Re-search"** button (bottom right)
2. Confirmation dialog appears:
   ```
   This will:
   1. Delete X file(s) from disk
   2. Tell Radarr to re-search for them
   
   Continue?
   ```
3. Click **Yes** to proceed

### What Happens Automatically (Per Movie)

For each queued movie, the tool performs these steps in sequence:

| Step | Action | What Changes |
|------|--------|--------------|
| 1 | **Lookup** | Find movie in Radarr by folder path |
| 2 | **Delete from disk** | `shutil.rmtree` removes the entire folder |
| 3 | **Unmonitor** | Radarr stops watching this movie temporarily |
| 4 | **Delete file record** | Radarr's database entry removed |
| 5 | **Re-monitor** | Radarr starts watching again |
| 6 | **Search** | Radarr triggers indexer searches |

**State transitions:**
```
QUEUED → DELETING → DELETED → RESEARCHING
```

### Step 6: Wait for Radarr to Re-acquire

After remediation completes:

1. Radarr searches its configured indexers
2. Finds a release for the movie
3. Sends it to your downloader (SAB, qBittorrent, etc.)
4. Downloader completes the download
5. **Pluck Movies** (with VERIFY_LEVEL=3) will catch corruption BEFORE import
   - If clean: imports the new copy
   - If corrupt: rejects → you remediate again until you get a good copy

### Step 7: Verify the Fix (Optional but Recommended)

After Radarr re-acquires the movie:

1. Switch to **"Live Scan (Start Fresh)"** mode
2. Click **"Start Scan"** with appropriate library selected
3. Wait for the scan to complete
4. Switch back to **Database View**
5. Filter to find the movie - it should now show:
   - **Verdict:** CLEAN ✓
   - **State:** REMEDIATED 🎉

---

## Recovery Scenarios

### "I queued the wrong movie!"

Before clicking "Delete + Re-search":
1. Right-click the movie → **"Mark as Skipped"** to remove from queue
2. Or filter to **"QUEUED"** and uncheck boxes

### "I want to skip a movie permanently"

1. Right-click → **"Mark as Skipped"**
2. State becomes `SKIPPED` - won't be queued again

### "Radarr can't find a clean copy"

The movie stays in `RESEARCHING` state. Options:

1. **Wait longer** - Radarr keeps trying based on its retry settings
2. **Check Radarr UI** - See if there are search errors
3. **Manual upload** - Find a copy elsewhere and use Pluck Movies to import
4. **Mark as Skipped** - Give up on automated remediation

### "The new copy is also corrupt!"

Pluck Movies (VERIFY_LEVEL=3) catches this before import. To remediate again:
1. Re-scan with this tool
2. Movie should still show CORRUPT
3. Click **"Delete + Re-search"** again
4. Radarr will try a different release

---

## Common Investigation Tasks

### Why is this movie marked CORRUPT?

1. Click on the movie row to select it
2. Click **"Show ffmpeg Log"** button
3. OR right-click → **"📄 Show ffmpeg Log"**

You'll see the actual ffmpeg error, e.g.:
```
File ended prematurely at position 1234567890
[matroska,webm @ 0x...] File ended prematurely
Error opening output file
```

### How big are my corrupt files?

1. Filter to **"CORRUPT"**
2. Click **"Size"** column header to sort by size
3. Look at the bottom status bar for total counts

### Has this movie been remediated before?

Look at the **Attempts** column in the table:
- **0** - Never remediated
- **1** - First time
- **2** - ⚠️ Second attempt (something went wrong before)
- **3+** - 🔴 Persistent issue - investigate before remediating again!

Multiple attempts often indicate:
- Indexer keeps providing the same bad release
- Pluck Movies rsync corruption (transfer-time issue)
- Upstream source is bad (the original encode is broken)

**If attempts >= 3, stop and investigate** before queuing another remediation cycle.

You can also check via CLI:
```powershell
python main.py list --corrupt
```

### Can I see what was done to a movie?

Currently you can see the current state. The full history is in the SQLite database (`repair.db`). To query directly:
```powershell
sqlite3 repair.db "SELECT folder_path, scan_state, remediation, attempts FROM files WHERE folder_path LIKE '%movie name%'"
```

---

## Identifying Systemic Issues with Attempts Column

The **Attempts** column tracks how many times you've remediated a movie. Use it to spot patterns:

### Pattern 1: Single Movie, High Attempts
**Symptom:** One movie keeps showing CORRUPT after multiple remediations

**Likely causes:**
- Indexer keeps grabbing the same bad release
- Source release is fundamentally broken
- Pluck Movies has rsync issue specifically with this file

**Action:**
1. Check the ffmpeg log - does the corruption pattern look the same?
2. If yes → blacklist this release in Radarr (when v2 has blocklist support)
3. Try manually finding a different release source
4. Or mark as SKIPPED and accept the loss

### Pattern 2: Many Movies, All Attempts=2
**Symptom:** Lots of movies showing 2 attempts, all freshly corrupt

**Likely causes:**
- **Pluck Movies rsync issue** - silently corrupting during NAS transfer
- SAB/par2 silent failure (the original 28YL pattern)
- Network instability during transfer

**Action:**
1. Stop bulk remediation - you'll just keep churning
2. Investigate Pluck Movies workflow
3. Check Pluck VERIFY_LEVEL=3 (should catch corruption pre-import)
4. Test rsync integrity manually

### Pattern 3: Attempts=1, Then Stays Clean
**Symptom:** Healthy remediation cycle - this is normal!

**Action:** Keep going! The system is working.

### Sorting by Attempts

Click the **Attempts** column header to sort by attempt count:
- Descending: See worst offenders first
- Use this view to identify movies that may have systemic issues

### Color Coding

The Attempts column uses colors to draw attention:
- **Plain (0-1)** - Normal
- **Bold orange (2)** - ⚠️ Worth checking
- **Bold red (3+)** - 🚨 Stop and investigate!

---

## Best Practices

### Before Your First Remediation

1. ✅ **Test scan first** - Run `python main.py scan --limit 10` to verify ffmpeg works
2. ✅ **Verify Radarr connection** - Check `.env` has correct URL/API key
3. ✅ **Set Pluck VERIFY_LEVEL=3** - Prevents re-import of bad copies
4. ✅ **Try ONE movie first** - Start small, verify the workflow works
5. ✅ **Check Radarr indexers** - Make sure they're configured and working

### Daily/Weekly Workflow

**Day 1: Initial Scan (long-running)**
```powershell
# Start scan, let it run overnight
python main.py scan
```

**Day 2: Review Results**
1. Open GUI in Database View
2. Filter: CORRUPT
3. Sort by Size (biggest first)
4. Review each one, queue what you want to fix

**Day 3: Remediate**
1. Click "Delete + Re-search"
2. Wait for Radarr to download replacements
3. Pluck Movies imports new copies

**Day 4: Verify**
1. Re-scan affected libraries
2. Check that fixed movies are now CLEAN

### Batch Size Recommendations

Don't queue too many at once:

| Library Size | Recommended Batch | Why |
|--------------|------------------|-----|
| 1-5 corrupt | All at once | Easy to manage |
| 10-20 corrupt | 5-10 per batch | Watch downloads |
| 50+ corrupt | 10 per batch | Don't overwhelm Radarr |

Use the CLI batch limit:
```powershell
python main.py remediate --max 10
```

### When to Use Live Scan vs Database View

**Use Database View when:**
- ✅ Reviewing previous scan results
- ✅ Selecting movies to remediate
- ✅ Inspecting corruption details
- ✅ Checking remediation status

**Use Live Scan when:**
- ✅ Watching new scans happen
- ✅ Verifying recently re-acquired movies
- ✅ Re-scanning specific libraries

---

## Status Indicators Cheat Sheet

### Verdict (Scan Result)

| Symbol | Verdict | Meaning | Action |
|--------|---------|---------|--------|
| 🟢 | CLEAN | File passed integrity check | Ignore |
| 🔴 | **CORRUPT** | Has structural corruption | **Queue + Remediate** |
| 🟡 | ERROR | ffmpeg couldn't process (real error) | Investigate manually |
| 🟠 | TIMEOUT | Scan exceeded timeout (file too big/NAS slow) | **Don't remediate!** Will auto-rescan |
| 🟣 | MISSING | Folder no longer exists on disk | Verify or delete record |
| ⚪ | EMPTY | No video file in folder | Usually safe to delete |
| ⏳ | SCANNING | Scan in progress | Wait |

### Remediation State

| State | Meaning | What to Do |
|-------|---------|-----------|
| NONE | Default - not scheduled | Queue if needed |
| QUEUED | Selected for remediation | Click "Delete + Re-search" |
| DELETED | File removed from disk | Wait for Radarr |
| RESEARCHING | Radarr searching | Wait for download |
| REMEDIATED | Got clean replacement ✓ | Done! |
| FAILED | Something went wrong | Check logs, retry |
| SKIPPED | Marked to ignore | Won't be touched |

---

## Right-Click Context Menu Reference

Right-click on any movie row to access:

| Action | When to Use |
|--------|-------------|
| 📁 Open Folder | Inspect file in Explorer |
| 📄 Show ffmpeg Log | See WHY it's corrupt |
| ➕ Queue for Remediation | Quick queue (CORRUPT only) |
| 📋 Copy Path | Get folder path for scripts/manual ops |

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+Q** or **Ctrl+W** | Quit app |
| **Esc** | Stop running scan |
| **Ctrl+R** | Refresh table |
| **Ctrl+F** | Focus search box |

---

## Troubleshooting

### "Movie not found in Radarr"

The folder path doesn't match Radarr's library entry.

**Solution:**
1. Open Radarr UI
2. Search for the movie manually
3. Add it if missing
4. Make sure folder name matches: `Movie Title (YYYY)`
5. Re-try remediation

### "Radarr returns no results"

Indexers aren't finding releases.

**Solution:**
1. Check Radarr → Settings → Indexers
2. Verify indexers are healthy
3. Check Radarr → Activity → Search History
4. Look for indexer-specific errors

### "Pluck imports a corrupt copy anyway"

VERIFY_LEVEL is too low.

**Solution:**
Set in `Pluck Movies/config/movies.py` and `config/tv_shows.py`:
```python
VERIFY_LEVEL = 3  # Full ffmpeg null-decode
```

### "Remediation marked FAILED"

Check the remediation log via CLI:
```powershell
sqlite3 repair.db "SELECT folder_path, remediation_log FROM files WHERE remediation = 'FAILED'"
```

Common causes:
- Folder access denied (NAS permission issue)
- Radarr API rate limit
- Network timeout

---

## Example Real-World Session

Here's what a typical remediation session looks like:

```
1. Launch GUI
   → python main.py
   → Opens in Database View showing 247 total movies

2. Filter and Investigate
   → Filter: CORRUPT (47 movies)
   → Sort by Size (descending)
   → Right-click "28 Years Later (2025)" → Show ffmpeg Log
   → See: "File ended prematurely at position 1234567890"
   → Confirms it's actually corrupt

3. Queue for Remediation
   → Check boxes: 28 Years Later, Naked (1993), Real Steel (2011)
   → Click "Queue for Remediation"
   → State: NONE → QUEUED for all 3

4. Verify Queue
   → Filter: Remediation = QUEUED
   → See exactly 3 movies queued
   → Looks correct ✓

5. Execute
   → Click "Delete + Re-search"
   → Confirm dialog: "Delete 3 files and re-search?"
   → Click Yes
   → Watch progress: "Deleting 28 Years Later... OK"
   → "Triggering Radarr search... OK (cmd 12345)"
   → Repeat for other 2

6. Wait
   → Radarr searches for ~5 minutes
   → SAB downloads each (~30-60 min each)
   → Pluck Movies imports verified copies

7. Verify Fix (next day)
   → Switch to Live Scan mode
   → Start Scan on A-H library
   → Wait for completion
   → Switch back to Database View
   → Filter: REMEDIATED
   → See 3 movies marked REMEDIATED ✓
```

---

## What's Saved in the Database

Each movie folder has a record with:

- **folder_path** - Where the movie lives
- **scan_state** - CLEAN/CORRUPT/ERROR/EMPTY/UNKNOWN
- **stderr_tail** - Why it's marked CORRUPT (the ffmpeg error)
- **remediation** - Current state in remediation workflow
- **attempts** - How many times we've tried to remediate
- **first_seen_at** - When it was first scanned
- **last_scan_at** - Most recent scan
- **last_scan_secs** - How long the scan took

This data persists across app restarts and is what makes the **Database View** so useful - you can review all your work anytime.

---

## Quick Summary

The tool does ONE thing well:
1. **Find corrupt movies** (via deep ffmpeg scan)
2. **Show them to you** (in a sortable, filterable table)
3. **Let you decide** which to fix (queue them)
4. **Execute the fix** (delete + tell Radarr to re-acquire)
5. **Track everything** (in SQLite, persistent across sessions)

Combined with **Pluck Movies (VERIFY_LEVEL=3)** for verification on import, you have a closed-loop system that systematically eliminates corrupted files from your library.

---

*For technical details and architecture, see [USERGUIDE.md](USERGUIDE.md)*
