"""Fetch Qt's own third-party attributions for the Qt modules the Windows build contains — once, by hand, committed.

    python packaging/fetch_qt_attributions.py      → packaging/qt-attributions/<module>/... (qt_attribution.json + licences)

Qt does not ship its third-party notices in the PySide6 wheels, yet the Qt DLLs contain code under other licences
(PCRE2, HarfBuzz, FreeType, libpng, libjpeg-turbo, libtiff, libwebp, zlib, md4c, double-conversion, Unicode data …;
packaging review of 22 Oct 2026). Qt keeps a qt_attribution.json next to every such component in its source tree;
this script copies those for the release tag of the bundled Qt, plus each licence file they name, from Qt's public
GitHub mirror. An entry that names no licence file uses the standard text in the module's LICENSES folder
(REUSE layout, one file per SPDX id), which is copied too. Attributions under tests/ describe Qt's own test
data, not code in the libraries, and are skipped. build.py turns them into the notices without the network.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

QT_VERSION = "6.12.0"
MODULES = ("qtbase", "qtsvg", "qtimageformats")
OUT = Path(__file__).resolve().parent / "qt-attributions"
API = "https://api.github.com/repos/qt/{module}/git/trees/v{version}?recursive=1"
RAW = "https://raw.githubusercontent.com/qt/{module}/v{version}/{path}"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "hotspot-lv-build"})
    return urllib.request.urlopen(req, timeout=60).read()


def entries(data) -> list[dict]:
    return data if isinstance(data, list) else [data]


def spdx_ids(expression: str) -> list[str]:
    """The licence ids of an SPDX expression: 'CC0-1.0 OR Apache-2.0' → ['CC0-1.0', 'Apache-2.0']."""
    return [t for t in re.split(r"[\s()]+", expression or "") if t and t not in ("OR", "AND", "WITH")]


def license_files(entry: dict, folder: Path) -> list[str]:
    """Paths (in the module) of the licence texts of one attribution entry."""
    files = entry.get("LicenseFiles") or ([entry["LicenseFile"]] if entry.get("LicenseFile") else [])
    if files:
        return [(folder / lf).as_posix() for lf in files]
    return [f"LICENSES/{i}.txt" for i in spdx_ids(entry.get("LicenseId", ""))]


def main() -> int:
    total = 0
    for module in MODULES:
        tree = json.loads(get(API.format(module=module, version=QT_VERSION)))
        if tree.get("truncated"):
            raise SystemExit(f"{module}: the tree listing was truncated")
        paths = [t["path"] for t in tree["tree"]
                 if t["path"].endswith("qt_attribution.json") and not t["path"].startswith("tests/")]
        for p in paths:
            dest = OUT / module / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = get(RAW.format(module=module, version=QT_VERSION, path=p))
            dest.write_bytes(raw)
            folder = Path(p).parent
            for e in entries(json.loads(raw.decode("utf-8"), strict=False)):   # some carry raw tabs
                for src in license_files(e, folder):
                    target = OUT / module / src
                    if target.exists():
                        continue
                    try:
                        data = get(RAW.format(module=module, version=QT_VERSION, path=src))
                    except Exception as ex:                       # a missing file is reported, not fatal
                        print(f"  {module}/{src}: {ex}")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            total += 1
        print(f"{module} {QT_VERSION}: {len(paths)} attribution files")
    (OUT / "SOURCE.txt").write_text(f"Qt {QT_VERSION} attributions from https://github.com/qt/<module> at tag "
                                    f"v{QT_VERSION} (modules: {', '.join(MODULES)}), fetched by fetch_qt_attributions.py\n",
                                    encoding="utf-8")
    print(f"{total} attribution files in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
