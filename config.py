"""Configuration loader for Repair Broken Media Files."""
import os
import socket
import sys
from pathlib import Path
from dotenv import load_dotenv

# Single source of truth for the app version. Bump this whenever a feature is
# added; it is shown in the window title and the CLI banner so you can always
# tell which build is running.
APP_VERSION = "1.7.2"

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).parent

if os.getenv("LAUNCHED_FROM_MEDIA_TOOLS_LAUNCHER"):
    load_dotenv(SCRIPT_DIR.parent / ".env", override=True)
    load_dotenv(SCRIPT_DIR / ".env", override=True)
else:
    load_dotenv(SCRIPT_DIR / ".env", override=True)

# Radarr connection
RADARR_HOST = os.getenv("RADARR_URL", "http://mforum-ms01-a.tail425a06.ts.net")
RADARR_PORT = os.getenv("RADARR_PORT", "8989")
RADARR_URL  = f"{RADARR_HOST.rstrip('/')}:{RADARR_PORT}"
RADARR_API  = os.getenv("RADARR_API", "")

# Email (deferred to v2)
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

# =============================================================================
# DATABASE
# =============================================================================
# Backend: 'sqlite' (single PC) or 'postgres' (multi-PC shared scanning)
DB_BACKEND = "sqlite"

# SQLite path (used when DB_BACKEND=sqlite)
DB_PATH = Path(__file__).parent / "repair.db"

# PostgreSQL connection string (used when DB_BACKEND=postgres)
# Set DATABASE_URL in .env — contains credentials so it doesn't belong here
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Postgres host fallback list — tried in order; first to connect wins.
# Lets you use LAN IP at home, Tailscale when remote, without changing .env.
POSTGRES_HOST_CANDIDATES = [
    "192.168.1.238",                    # local LAN (fastest when on home network)
    "casaos.tail425a06.ts.net",         # Tailscale DNS name
    "100.102.164.45",                   # Tailscale IP (last resort)
]

# Worker ID — tags DB records with which PC did the scan (multi-PC mode)
WORKER_ID = socket.gethostname()  # override here if needed

# Logs directory
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def get_library_roots():
    """
    Discover library roots from Movie Library Compressor/compressor.yaml.
    Returns list of Path objects for each unique library_path under hosts:.
    Falls back to default roots if compressor.yaml is not found.
    """
    try:
        import yaml
        compressor_yaml = Path(__file__).parent.parent / "Movie Library Compressor" / "compressor.yaml"
        
        if compressor_yaml.exists():
            with open(compressor_yaml, "r") as f:
                cfg = yaml.safe_load(f)
            
            hosts = cfg.get("hosts", {}) or {}
            seen = set()
            roots = []
            
            for host_cfg in hosts.values():
                lp = host_cfg.get("library_path")
                if lp and lp not in seen:
                    seen.add(lp)
                    roots.append(Path(lp))
            
            if roots:
                return roots
    except Exception as e:
        print(f"Warning: Could not load compressor.yaml: {e}")
    
    # Fallback to defaults
    return [
        Path("Z:/Movies/A-H"),
        Path("Z:/Movies/I-S"),
        Path("Z:/Movies/T-Z"),
    ]
