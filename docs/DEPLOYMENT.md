# Deployment Guide: Repair Broken Media Files

How to package the app as a standalone Windows `.exe` and deploy it to other PCs without requiring Python or Pipenv on the target.

This document covers the **build PC** (where the `.exe` is created) and the **target PC** (where it runs).

---

## Quick Reference

| Where | What you need |
|---|---|
| **Build PC** | Python 3.11+, Pipenv, this repo cloned, `pipenv install --dev` |
| **Target PC** | The `dist\` folder, `ffmpeg.exe` on PATH, network access to NAS and Radarr |

---

## Building the .exe

### One-Command Build

From this project's root directory:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

That's it. The script handles dependency installation, cleanup, and the PyInstaller invocation.

### What Gets Built

After a successful run you'll have a `dist\` folder containing:

```
dist\
├── RepairBrokenMedia.exe       # The standalone executable (~52 MB)
├── .env.example                # Template config (copy to .env)
├── README.md                   # Quick reference
└── docs\
    ├── USERGUIDE.md
    ├── WORKFLOW.md
    ├── DEPLOYMENT.md           # this file
    ├── DISASTER_RECOVERY.md
    └── FUTURE.md
```

The `.exe` includes:
- Python 3.11 runtime
- PySide6 GUI framework
- `psycopg2-binary` (for PostgreSQL backend)
- `requests` (for Radarr API)
- `python-dotenv` (for .env loading)
- All your application code

### Manual Build (Without the Script)

If you need to debug or customize the build:

```powershell
# 1. Install dependencies
pipenv install --dev

# 2. Clean previous build
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# 3. Build using the venv's PyInstaller
$venv = (pipenv --venv).Trim()
& "$venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean repair_broken_media.spec
```

### Build Time

- First build: 1-3 minutes (PyInstaller analyzes everything)
- Subsequent builds: ~30 seconds (incremental)

---

## Deploying to a Target PC

### Prerequisites on the Target PC

1. **Windows 10/11** (64-bit)
2. **ffmpeg** must be installed and on PATH
   - Easiest: `winget install ffmpeg`
   - Or download from https://ffmpeg.org and add `ffmpeg\bin` to PATH
   - Verify: `ffmpeg -version`
3. **Network access** to:
   - Your NAS / movie library
   - Radarr server (if you'll use remediation)
   - PostgreSQL server (if `DB_BACKEND=postgres`)

No Python install needed. No Pipenv. The exe is self-contained.

### Deployment Steps

#### 1. Copy the dist folder

Copy the entire `dist\` folder to the target PC. Suggested locations:
- `C:\Tools\RepairBrokenMedia\` (recommended)
- `D:\Apps\RepairBrokenMedia\`
- Any folder the user has read/write permissions on

The folder must be writable because:
- SQLite mode writes `repair.db` next to the exe
- Logs go to `logs\` next to the exe
- `.env` lives next to the exe

#### 2. Configure `.env`

In the deployment folder:

```powershell
# Copy the template
Copy-Item .env.example .env

# Edit with notepad / your editor
notepad .env
```

Minimum required values:

```bash
RADARR_URL=http://mforum-ms01-a:8989
RADARR_API=your-api-key-here
```

For multi-PC mode (recommended when running on additional PCs):

```bash
DB_BACKEND=postgres
DATABASE_URL=postgresql://casaos:casaos@192.168.1.238:5432/casaos
```

When `DB_BACKEND=postgres`:
- All PCs share the same database (the central PostgreSQL)
- The hostname is auto-detected as `WORKER_ID` (no need to set explicitly)
- Each PC's scan results are tagged with its hostname in the `worker_id` column

#### 3. Run the App

Just double-click `RepairBrokenMedia.exe`, or from PowerShell:

```powershell
cd C:\Tools\RepairBrokenMedia
.\RepairBrokenMedia.exe
```

You should see the GUI launch in Database View showing your existing scan history (if Postgres backend) or an empty database (if SQLite, fresh).

#### 4. CLI Mode

The same exe works as a CLI tool:

```powershell
# Scan with 4 parallel workers
.\RepairBrokenMedia.exe scan --workers 4

# List corrupt files
.\RepairBrokenMedia.exe list --corrupt

# Queue all corrupt files
.\RepairBrokenMedia.exe queue --all-corrupt

# Dry-run remediation
.\RepairBrokenMedia.exe remediate --dry-run

# Actually remediate (max 5 at a time)
.\RepairBrokenMedia.exe remediate --max 5
```

---

## Multi-PC Deployment Pattern

The whole point of the Postgres backend is running on multiple PCs simultaneously to share the scan workload.

### Recommended Topology

```
┌────────────────────────────────────────┐
│  Central PostgreSQL (casaos:5432)       │
│  ┌──────────────────────────────────┐  │
│  │ database: casaos                  │  │
│  │ tables: repair_files, repair_runs │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
                  ▲
                  │ shared state
        ┌─────────┴──────────┬──────────────┐
        │                    │              │
   ┌────┴────┐          ┌────┴────┐    ┌────┴────┐
   │ ms01-a  │          │ gt1-a   │    │ nyc     │
   │ (GUI)   │          │ (worker)│    │ (worker)│
   └─────────┘          └─────────┘    └─────────┘
   Radarr host          Idle PC #1     Idle PC #2
   (interactive)        (background)   (background)
```

### Step-By-Step Multi-PC Setup

1. **Build the exe on one PC** (`build.ps1`)
2. **Migrate any existing SQLite data** to Postgres on that PC:
   ```powershell
   pipenv run python migrate_sqlite_to_postgres.py --execute
   ```
3. **Copy `dist\` to each target PC**
4. **On each target**, configure `.env` with `DB_BACKEND=postgres`
5. **Verify** each target can reach the Postgres server (Tailscale, LAN, or both)
6. **Run the exe** on each target — they'll all see the same data
7. **Each PC scans in its own scan window**, results land in the shared DB

### Important Caveats (v1.0)

The current implementation gives you:

- ✅ Shared scan results visible to all PCs
- ✅ Shared queue (queue from one PC, remediate from another)
- ✅ Per-PC tracking via `worker_id` column

But it does NOT yet give you:

- ❌ **Automatic work-claim distribution.** If two PCs both click Start Scan, they'll both walk the same library folders. The first to finish a folder writes its result; the second's later write may overwrite. This usually works out fine (same file, same result) but isn't optimal.

To work around this until Phase 3-5 of the multi-PC plan is built (see `FUTURE.md`):

**Option A — Time-multiplex scans:**
- Only one PC runs an active scan at a time
- Other PCs do GUI/remediation work

**Option B — Manually partition by library root:**
```powershell
# On ms01-a:
.\RepairBrokenMedia.exe scan --root "Z:\Movies\A-H"

# On gt1-a (at the same time):
.\RepairBrokenMedia.exe scan --root "Z:\Movies\I-S"

# On nyc:
.\RepairBrokenMedia.exe scan --root "Z:\Movies\T-Z"
```

Each PC scans a different subtree, no overlap.

---

## Updating the Deployment

When new versions of the app are released:

1. **On the build PC:** `git pull && powershell -File build.ps1`
2. **On each target PC:** Replace `RepairBrokenMedia.exe` with the new one
3. **Keep the existing `.env` and `repair.db`** on each target — they don't change

The exe is fully self-contained, so updating is just a file copy. No reinstall, no migration.

---

## Troubleshooting

### "ffmpeg not found on PATH"

```powershell
# Verify ffmpeg is accessible
ffmpeg -version

# If not found, install:
winget install ffmpeg

# Or specify full path in scanner.py if you can't change PATH
```

### "Could not connect to PostgreSQL"

The Postgres host fallback list in `config.py` defaults to:
- `192.168.1.238` (local LAN)
- `casaos` (Tailscale DNS)
- `100.102.164.45` (Tailscale IP)

If none reach your DB:

1. Verify Tailscale is running: `tailscale status`
2. Test connectivity: `Test-NetConnection casaos -Port 5432`
3. Edit `config.py` to add your specific host (and rebuild)
4. Or override entirely with `DATABASE_URL` in `.env`

### Antivirus flags the exe

PyInstaller-built exes are sometimes flagged by Windows Defender or third-party AV as suspicious because the bootloader pattern looks unusual. Options:

1. **Add an exclusion** for `RepairBrokenMedia.exe` or its folder
2. **Sign the exe** with a code-signing certificate (advanced)
3. **Build with `console=True`** (current default) — fewer false positives than windowed mode

### "Permission denied" writing repair.db

The deployment folder must be writable. Avoid:
- `C:\Program Files\` (admin-only writable)
- Network shares with read-only access

Use:
- `C:\Users\YourName\Tools\` (user-writable)
- `C:\Tools\RepairBrokenMedia\` (if folder ACL grants write)

### App won't start (no error visible)

Run from PowerShell to see error output:

```powershell
cd C:\Tools\RepairBrokenMedia
.\RepairBrokenMedia.exe
```

Common causes:
- Missing `.env` file — copy from `.env.example` and edit
- Missing ffmpeg
- Postgres unreachable but `DB_BACKEND=postgres` set

### Slow first launch

The PyInstaller `runtime_tmpdir=None` setting means the bootloader extracts deps to `%TEMP%` on first launch (~5 seconds). Subsequent launches reuse it.

To switch to single-file mode (slower launch but simpler distribution), edit `repair_broken_media.spec` and add `runtime_tmpdir='_internal'` — or accept the current behavior since it's a one-time hit.

---

## Spec File Reference

`repair_broken_media.spec` is the build recipe. Key settings:

| Setting | Value | Why |
|---|---|---|
| `console` | `True` | Shows terminal so users see CLI output and debug info |
| `upx` | `True` | Compresses binaries (smaller exe) |
| `excludes` | `tkinter`, `WebEngine`, `Multimedia`, etc. | Slim down PySide6 to just QtWidgets |
| `hiddenimports` | `psycopg2`, `_yaml` | Modules PyInstaller can't auto-detect |
| `datas` | `.env.example` | Bundle the template config inside the exe |

Edit the spec if you need to add icons, change name, or include extra files.

---

## CI/CD (Future)

Currently builds are manual on a developer machine. A GitHub Actions workflow could:

1. Build on every push to `main`
2. Upload `RepairBrokenMedia.exe` as a release artifact
3. Auto-version using git tags

This is in `FUTURE.md` as a low-priority enhancement. Manual builds are fine for now since the deployment audience is small.

---

## Verification Checklist

Before declaring a deployment ready, run through this on the target PC:

- [ ] `ffmpeg -version` works
- [ ] `RepairBrokenMedia.exe` launches without errors
- [ ] GUI opens and shows expected data (Database View)
- [ ] Switch to Live Scan mode — scan controls enable
- [ ] Click Start Scan with `--limit 1` style brief test → verdict appears
- [ ] CLI test: `RepairBrokenMedia.exe list --corrupt` runs
- [ ] Right-click context menu opens on a row
- [ ] (If Postgres) `worker_id` shows this PC's hostname in the database
- [ ] Stop button works
- [ ] App quits cleanly (terminal prompt returns immediately)

If all pass — you're deployed.

---

*See [USERGUIDE.md](USERGUIDE.md) for day-to-day usage. See [FUTURE.md](FUTURE.md) for upcoming features including full multi-PC work distribution.*
