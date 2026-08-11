# Build a standalone Windows .exe of Repair Broken Media Files.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Requires:
#   - Python 3.11+ on PATH
#   - Pipenv installed (pip install pipenv)
#   - Run from inside the project directory
#
# Notes:
#   - We deliberately DO NOT use `$ErrorActionPreference = "Stop"`, because
#     pipenv/pip write harmless progress to stderr, which that setting would
#     treat as fatal. Instead we check $LASTEXITCODE explicitly after each
#     external command.

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Repair Broken Media Files - Build Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# Verify we're in the right directory
if (-not (Test-Path "main.py")) { Fail "main.py not found. Run this script from the project root." }
if (-not (Test-Path "repair_broken_media.spec")) { Fail "repair_broken_media.spec not found." }

# Step 1: Ensure dependencies are installed (pipenv writes to stderr; that's OK)
Write-Host "[1/5] Ensuring app dependencies are installed..." -ForegroundColor Yellow
pipenv install 2>&1 | ForEach-Object { "$_" } | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "pipenv install failed (exit $LASTEXITCODE)" }
Write-Host "  OK"
Write-Host ""

# Step 2: Locate the venv so we build with the interpreter that HAS the app deps
Write-Host "[2/5] Locating virtualenv..." -ForegroundColor Yellow
$VenvPath = (pipenv --venv 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $VenvPath) { Fail "Could not locate pipenv venv (is pipenv installed?)" }
$VenvPath = $VenvPath.Trim()
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Fail "venv python not found at $VenvPython" }
Write-Host "  venv: $VenvPath"
Write-Host ""

# Step 3: Ensure PyInstaller is available INSIDE the venv (not just system Python).
# This is the common failure: the venv has PySide6/psycopg2 but not PyInstaller.
Write-Host "[3/5] Ensuring PyInstaller is in the venv..." -ForegroundColor Yellow
& $VenvPython -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller missing - installing into venv..."
    & $VenvPython -m pip install pyinstaller 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Failed to install PyInstaller into the venv" }
}
# Sanity: confirm the venv can import the app's runtime deps too.
& $VenvPython -c "import PySide6, psycopg2, dotenv, requests, PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "venv is missing a required dependency (PySide6/psycopg2/dotenv/requests/PyInstaller)" }
Write-Host "  OK"
Write-Host ""

# Step 4: Clean previous build artifacts
Write-Host "[4/5] Cleaning previous build..." -ForegroundColor Yellow
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
Write-Host "  Cleaned build/ and dist/"
Write-Host ""

# Step 5: Build with the venv's Python, then stage support files
Write-Host "[5/5] Building executable with PyInstaller (may take 1-3 minutes)..." -ForegroundColor Yellow
& $VenvPython -m PyInstaller --noconfirm repair_broken_media.spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed (exit $LASTEXITCODE)" }
Write-Host ""

$DistDir = Join-Path (Get-Location) "dist"
$ExePath = Join-Path $DistDir "RepairBrokenMedia.exe"
if (-not (Test-Path $ExePath)) { Fail "Expected exe not found at $ExePath" }

# Copy supporting files into dist/ for easy deployment
Copy-Item ".env.example" -Destination $DistDir -Force
Copy-Item "README.md"    -Destination $DistDir -Force
if (Test-Path "docs") { Copy-Item -Recurse "docs" -Destination $DistDir -Force }

# Report version + size, and verify the exe actually runs.
$ExeSize = (Get-Item $ExePath).Length / 1MB
$BuiltVersion = (& $ExePath version 2>&1 | Select-Object -First 1)

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host " Build successful!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Output:  $ExePath"
Write-Host ("  Size:    {0:N1} MB" -f $ExeSize)
Write-Host "  Version: $BuiltVersion"
Write-Host ""
Write-Host "Files in dist/:"
Get-ChildItem $DistDir | Select-Object Name, @{N='Size(MB)'; E={[math]::Round($_.Length/1MB, 2)}} | Format-Table

Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Copy the contents of dist/ to the target PC"
Write-Host "  2. On target PC: copy .env.example to .env and edit"
Write-Host "  3. Make sure ffmpeg is on the target PC's PATH"
Write-Host "  4. Run RepairBrokenMedia.exe"
Write-Host ""
Write-Host "See docs/DEPLOYMENT.md for details." -ForegroundColor Cyan
