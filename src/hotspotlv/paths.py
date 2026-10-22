"""Where data lives.

* Shipped data (small, read-only, inside the installed package): `package_data()`
  — works from a source checkout, a wheel and a PyInstaller bundle.
* Downloads and caches (SMHI archives, PVGIS responses, weather for other years
  or stations): `cache_dir()` — a writable per-user folder, override with the
  environment variable HOTSPOTLV_CACHE.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


def package_data() -> Path:
    return Path(str(files("hotspotlv") / "data"))


def cache_dir(create: bool = True) -> Path:
    """The per-user cache. create=False only names it — nothing is written to the user's profile until a
    download or a built weather year actually needs the folder (packaging review of 22 Oct 2026)."""
    d = Path(os.environ.get("HOTSPOTLV_CACHE", Path.home() / ".hotspotlv"))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def raw_dir() -> Path:
    d = cache_dir() / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d
