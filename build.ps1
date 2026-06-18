# Build a standalone Windows .exe of Repair Broken Media Files.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Requires:
#   - Python 3.11+ on PATH
#   - Pipenv installed (pip install pipenv)
#   - Run from inside the project directory

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Repair Broken Media Files - Build Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Verify we're in the right directory
if (-not (Test-Path "main.py")) {
    Write-Host "ERROR: main.py not found. Run this script from the project root." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "repair_broken_media.spec")) {
    Write-Host "ERROR: repair_broken_media.spec not found." -ForegroundColor Red
    exit 1
}

# Step 1: Install dependencies (skip if already installed)
Write-Host "[1/4] Ensuring dependencies are installed..." -ForegroundColor Yellow
pipenv install --dev 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pipenv install failed" -ForegroundColor Red
    exit 1
}
Write-Host "  OK"
Write-Host ""

# Step 2: Locate the venv (so we can call PyInstaller directly with right interpreter)
$VenvPath = (pipenv --venv).Trim()
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: venv python not found at $VenvPython" -ForegroundColor Red
    exit 1
}

# Step 3: Clean previous build artifacts
Write-Host "[2/4] Cleaning previous build..." -ForegroundColor Yellow
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
Write-Host "  Cleaned build/ and dist/"
Write-Host ""

# Step 4: Run PyInstaller via the venv's Python (guarantees PySide6/psycopg2/etc are visible)
Write-Host "[3/4] Building executable with PyInstaller (may take 1-3 minutes)..." -ForegroundColor Yellow
& $VenvPython -m PyInstaller --noconfirm repair_broken_media.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller build failed" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 4: Stage support files alongside the exe
Write-Host "[4/4] Staging deployment package..." -ForegroundColor Yellow

$DistDir = Join-Path (Get-Location) "dist"
$ExePath = Join-Path $DistDir "RepairBrokenMedia.exe"

if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: Expected exe not found at $ExePath" -ForegroundColor Red
    exit 1
}

# Copy supporting files into dist/ for easy deployment
Copy-Item ".env.example" -Destination $DistDir -Force
Copy-Item "README.md"    -Destination $DistDir -Force
if (Test-Path "docs") {
    Copy-Item -Recurse "docs" -Destination $DistDir -Force
}

# Report final size
$ExeSize = (Get-Item $ExePath).Length / 1MB
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host " Build successful!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Output: $ExePath"
Write-Host ("  Size:   {0:N1} MB" -f $ExeSize)
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
