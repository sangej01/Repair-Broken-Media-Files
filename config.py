"""Configuration loader for Repair Broken Media Files."""
import os
import socket
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Radarr connection
RADARR_URL = os.getenv("RADARR_URL", "http://mforum-ms01-a:8989")
RADARR_API = os.getenv("RADARR_API", "")

# Email (deferred to v2)
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

# ── Database backend selection ────────────────────────────────────────────────
# 'sqlite' (default) — single PC, repair.db in this directory
# 'postgres'         — shared LAN database for multi-PC scanning
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower().strip()

# SQLite database path (used when DB_BACKEND=sqlite)
DB_PATH = Path(__file__).parent / "repair.db"

# PostgreSQL connection string (used when DB_BACKEND=postgres)
# Example: postgresql://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Postgres host fallback list — tried in order; first to connect wins.
# Lifted from Movie-Library-Compressor pattern: lets us use LAN IP at home,
# Tailscale DNS/IP when remote, without changing .env per location.
# Leave empty to use DATABASE_URL host as-is.
POSTGRES_HOST_CANDIDATES = [
    "192.168.1.238",       # local LAN (fastest when on home network)
    "casaos",              # Tailscale DNS name
    "100.102.164.45",      # Tailscale IP (last resort)
]

# Worker identification — used to track which PC scanned which folder
# when DB_BACKEND=postgres (multi-PC mode).
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())

# Logs directory
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def get_library_roots():
    """
    Discover library roots from Movie-Library-Compressor/compressor.yaml.
    Returns list of Path objects for each unique library_path under hosts:.
    Falls back to default roots if compressor.yaml is not found.
    """
    try:
        import yaml
        compressor_yaml = Path(__file__).parent.parent / "Movie-Library-Compressor" / "compressor.yaml"
        
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
