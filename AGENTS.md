# AGENTS.md

Instructions for AI coding agents working in this repository.

## Session start: check for a handoff

At the start of a conversation, look in `.kilo/plans/` for a handoff file
(e.g. `HANDOFF-*.md`) whose header says `Status: ACTIVE`. If one exists:

1. Read the handoff first, then read any plan file it references.
2. Resume from the "next action" / first unchecked task it describes.
3. When that work is fully finished (committed/pushed as the handoff intends),
   close the handoff so it never misleads a later session:
   - set its header to `Status: DONE`, and
   - move the file to `.kilo/plans/archive/` (create the folder if needed).

Ignore any handoff marked `Status: DONE`, any file already under
`.kilo/plans/archive/`, and (to be safe) any handoff whose tasks are clearly all
complete even if someone forgot to mark it. Never redo finished work. Only ever
act on ONE active handoff; if multiple are ACTIVE, ask which to use.

If there is no active handoff file, proceed normally with the user's request.

## Project quick facts

- Windows / PowerShell environment.
- App is a PySide6 GUI: `main.py` -> `app/main_window.py`; scanning in
  `scanner.py`; SQLite access in `db.py` (canonical DB: `repair.db`).
- **Only one scanner at a time.** Never start a second scan (GUI or CLI) while
  the app is scanning — concurrent SQLite writers collide and crash the app.
  Read-only DB inspection via a short-timeout `sqlite3` connect is fine.
- Validate Python changes with `python -m py_compile <file>`.
- The built exe is PyInstaller-frozen; a windowless parent PID + the real app
  PID is normal (not two instances).
