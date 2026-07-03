"""Load the project's ``config.yaml`` (secrets + global settings).

``config.yaml`` lives at the repo root, is gitignored (see
``config.yaml.example``), and holds secrets like API keys. Everything here is
optional: a missing file or key just yields ``None`` so callers can fall back to
other sources.

Precedence for secrets is **environment variable first, config file second** —
a shell override (``$env:GEMINI_API_KEY = ...``) always wins over the file,
which is handy for CI or a one-off run without editing ``config.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_FILENAME = "config.yaml"

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_API_KEY_CONFIG = "gemini_api_key"


def repo_root() -> Path:
    """Repo root = the parent of the ``modules`` package (…/modules/common/config.py)."""
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    """Default location of ``config.yaml`` (repo root)."""
    return repo_root() / CONFIG_FILENAME


def load_config(path: str | Path | None = None) -> dict:
    """Return the parsed ``config.yaml`` as a dict, or ``{}`` if it is absent.

    ``path`` defaults to :func:`config_path`; pass an explicit path in tests.
    """
    p = Path(path) if path is not None else config_path()
    if not p.is_file():
        return {}
    import yaml  # local import so the dep is only needed when a config exists

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_secret(config_key: str, env_var: str, path: str | Path | None = None) -> str | None:
    """Read a secret from ``env_var`` first, then ``config.yaml[config_key]``."""
    val = os.environ.get(env_var)
    if val:
        return val
    val = load_config(path).get(config_key)
    return str(val) if val else None


def get_gemini_api_key(path: str | Path | None = None) -> str | None:
    """The Gemini API key from ``$GEMINI_API_KEY`` or ``config.yaml``."""
    return get_secret(GEMINI_API_KEY_CONFIG, GEMINI_API_KEY_ENV, path)
