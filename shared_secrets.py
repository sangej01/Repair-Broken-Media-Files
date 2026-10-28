"""Load external shared credentials without bundling or displaying them."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values


def load_shared_secrets(
    path: str | Path | None = None, *, override: bool = True
) -> Path | None:
    """Load nonempty credentials into this process; return the file used.

    Explicit path wins over MEDIA_TOOLS_SECRETS_FILE, then ancestor discovery.
    Missing automatic discovery is a no-op; a configured missing file raises.
    Interpolation is disabled so password characters remain literal.
    """
    configured = path if path is not None else os.environ.get(
        "MEDIA_TOOLS_SECRETS_FILE"
    )
    if configured is not None:
        secrets_path = Path(configured).expanduser().resolve()
        if not secrets_path.is_file():
            raise FileNotFoundError("Configured shared secrets file does not exist.")
    else:
        anchor = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        secrets_path = next(
            (
                parent / "common" / ".secrets.env"
                for parent in anchor.resolve().parents
                if (parent / "common" / ".secrets.env").is_file()
            ),
            None,
        )
        if secrets_path is None:
            return None

    values = dotenv_values(secrets_path, encoding="utf-8-sig", interpolate=False)
    for key, value in values.items():
        if value and (override or key not in os.environ):
            os.environ[key] = value
    return secrets_path
