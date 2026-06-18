"""Configuration loader for Repair Broken Media Files."""
import os
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

# Database path
DB_PATH = Path(__file__).parent / "repair.db"

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
