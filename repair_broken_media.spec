# -*- mode: python ; coding: utf-8 -*-
# Build with: pipenv run pyinstaller repair_broken_media.spec
# Or use the helper: powershell -File build.ps1

block_cipher = None

# Bundled data files (paths inside the exe).
# We deliberately do NOT bundle .env or repair.db — those stay alongside the exe
# so users can configure per-machine without rebuilding.
datas = [
    (".env.example", "."),
]

# Hidden imports — modules PyInstaller cannot detect via static analysis.
# psycopg2 has dynamic imports; PySide6 plugins are auto-discovered but listing
# them here helps when running from minimal environments.
hiddenimports = [
    "psycopg2",
    "psycopg2.extras",
    "psycopg2._psycopg",
    "yaml",
    "_yaml",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude things we don't use to slim down the exe
        "tkinter",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RepairBrokenMedia",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=False -> no terminal window for GUI mode
    # console=True  -> shows terminal (useful for CLI commands and debugging)
    # We use console=True so users can see scan progress in CLI mode and
    # debug output even when launching the GUI. Keeps things simple.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
