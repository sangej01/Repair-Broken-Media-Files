# Cheat Sheet — the whole app on one page

Lost in the moving parts? Read this. It's the entire app in one loop.

---

## The app is ONE loop

```
   SCAN   →   FIX   →   VERIFY
 (find bad) (replace) (confirm good)
      ↑___________________|
        (repeat as needed)
```

- **SCAN** — the app decodes each movie and tells you which are bad.
- **FIX** — you delete the bad ones; Radarr downloads replacements.
- **VERIFY** — you re-scan the replacements to prove they're actually good.

Everything else in the app is just a detail inside one of these three steps.

---

## The two words that confuse everyone

Every movie has **two separate labels**. Mixing them up is what makes the app feel chaotic.

| Label | Answers | Who sets it | Example values |
|-------|---------|-------------|----------------|
| **Status** | *Is the file good?* | the scanner | CLEAN, CORRUPT, TIMEOUT |
| **Remediation** | *What have I done about it?* | you + Radarr | NONE, RESEARCHING, SKIPPED |

They move **independently**. A movie can be **CORRUPT + RESEARCHING** at the same time — that just means "the old file was bad, and a new one is on the way." That is normal, not a contradiction.

---

## The 4 buttons you actually use

In this order, every time:

1. **Start Scan** — find the bad files. *(SCAN)*
2. **Re-search all Group A** / **Inspect all Group B** — replace the bad files. *(FIX)*
3. **Check Re-downloads** — see which replacements have arrived (**Imported**). *(VERIFY, part 1)*
4. **Re-scan** the Imported ones — confirm they're good. *(VERIFY, part 2)*

Then read the result of step 4:
- **CLEAN** → fixed. Done forever.
- **CORRUPT again** → the replacement is also bad → send it back through FIX.

---

## The ONE rule people forget

> **"Radarr Imported" does NOT mean the file is good.**
> Radarr only checks the name and quality — it never watches the video.
> **The re-scan (step 4) is the only thing that proves a file is healthy.**

---

## Group A vs. Group B (only matters at FIX)

When a file is CORRUPT, the **Reason** column starts with a label telling you which kind:

- **Group A** (`[Incomplete / truncated]`, etc.) — a download accident. The **same** release will be fine. → **Re-search all Group A**.
- **Group B** (`[Broken container]`, `[Encoder artifact]`, etc.) — the release itself is bad. The same release won't help; you need a **different** one. → **Inspect all Group B** (it blocklists the bad release and finds another).

If you're not sure: right-click the movie → **Deep Inspect** and it tells you what to do.

---

## Everything else is an "exception handler"

Deep Inspect, Full Deep Decode, Mark CLEAN, Mark as Skipped, blocklist, the Corruption-type filter, stall settings — you only reach for these when the basic loop hits a weird case. **You do not need them for the normal flow.**

---

*Next level of detail:* **[IDIOTS_GUIDE.md](IDIOTS_GUIDE.md)** (beginner) ·
**[WORKFLOW_CHECKLIST.md](WORKFLOW_CHECKLIST.md)** (step-by-step) ·
**[INTERFACE.md](INTERFACE.md)** (every control) · **[MANUAL.md](MANUAL.md)** (scenarios & troubleshooting)
