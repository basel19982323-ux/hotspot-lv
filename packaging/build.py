"""Build the Windows release of Hotspot LV, check it, and zip it.

    pip install -e ".[app,build]"
    python packaging/build.py                 → dist/HotspotLV/ and dist/HotspotLV-<version>-win64.zip (+ .sha256)

Steps (each one stops the build if it fails):
  1. PyInstaller with packaging/hotspot-lv.spec (one folder, windowed, icon, version resource)
  2. next to HotspotLV.exe: LICENSE (MIT, this program) and THIRD-PARTY-NOTICES.txt — every bundled Python
     distribution with its version and licence text, read from its installed metadata, plus the Qt / PySide6 (LGPL v3)
     note on where its source is available. A binary that ships LGPL and BSD libraries must carry their notices.
  3. the frozen program checks itself: HotspotLV.exe --self-test build/self-test (exit code 0 = every step passed)
  4. the zip and its SHA-256
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP = DIST / "HotspotLV"
SELF_TEST = ROOT / "build" / "self-test"
LICENCE_FILE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
QT_NOTE = """\
Qt / Qt for Python (PySide6, shiboken6)
  Licence: GNU Lesser General Public License v3 (LGPL-3.0-only), see the PySide6 entry's text below.
  This program uses Qt for Python unmodified, as separate dynamic libraries in the _internal folder, so they can be
  replaced by another build of the same version. The source code of Qt for Python and Qt is available at
  https://code.qt.io and https://download.qt.io/official_releases/QtForPython/ (version as listed below).
"""


def version() -> str:
    scope: dict = {}
    exec((ROOT / "src" / "hotspotlv" / "__init__.py").read_text(encoding="utf-8"), scope)
    return scope["__version__"]


def bundled_distributions() -> list[md.Distribution]:
    """Every installed distribution that provides a top-level package or module found in the built _internal folder."""
    internal = APP / "_internal"
    names = {p.name.split(".")[0] for p in internal.iterdir()}         # packages with binaries or data: folders
    import ast                                                          # pure-Python packages live in the archive:
    _, modules = ast.literal_eval((ROOT / "build" / "hotspot-lv" / "PYZ-00.toc").read_text(encoding="utf-8"))
    names |= {entry[0].split(".")[0] for entry in modules}              # PyInstaller's own table of what it bundled
    owners = md.packages_distributions()
    found: dict[str, md.Distribution] = {}
    for top in names:
        for dist_name in owners.get(top, []):
            try:
                d = md.distribution(dist_name)
            except md.PackageNotFoundError:
                continue
            found[d.metadata["Name"].lower()] = d
    found.pop("hotspot-lv", None)                    # this program: its own LICENSE sits next to the exe
    return sorted(found.values(), key=lambda d: d.metadata["Name"].lower())


def licence_text(d: md.Distribution) -> str:
    texts = []
    for f in d.files or []:
        stem = f.name.upper().split(".")[0]
        in_licence_folder = f.parent.name.lower() in ("licenses", "licences")
        if (stem.startswith(LICENCE_FILE_NAMES) or in_licence_folder) and f.suffix.lower() in ("", ".txt", ".md", ".rst"):
            try:
                texts.append(f"--- {f.name} ---\n" + Path(d.locate_file(f)).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(texts)


def write_notices(path: Path) -> list[str]:
    lines = [f"Hotspot LV {version()} — third-party software bundled in this Windows build",
             "=" * 78, "",
             "Hotspot LV itself is MIT-licensed (see LICENSE). The Windows build also contains the Python runtime and",
             "the libraries below, each under its own licence. Python itself: Python Software Foundation License",
             f"(PSF-2.0), Python {sys.version.split()[0]} — https://docs.python.org/3/license.html", "", QT_NOTE]
    missing = []
    for d in bundled_distributions():
        meta = d.metadata
        licence = meta.get("License-Expression") or meta.get("License") or ""
        if len(licence) > 120:                       # some distributions put the whole licence text here
            licence = licence.splitlines()[0][:120] + " …"
        classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        text = licence_text(d)
        if not text:
            missing.append(meta["Name"])
        lines += ["-" * 78, f"{meta['Name']} {meta['Version']}",
                  f"  Licence: {licence or ', '.join(classifiers) or 'see the project'}",
                  f"  Source (used unmodified): https://pypi.org/project/{meta['Name']}/{meta['Version']}/",
                  f"  Home: {meta.get('Home-page') or next((u.split(', ', 1)[-1] for u in meta.get_all('Project-URL') or []), '')}",
                  "", text or "  (no licence file in the installed distribution — see the project's home page)", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return missing


def run(cmd: list[str], **kw) -> None:
    print("»", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "hotspot-lv.spec"), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")], cwd=ROOT)
    shutil.copy2(ROOT / "LICENSE", APP / "LICENSE.txt")
    missing = write_notices(APP / "THIRD-PARTY-NOTICES.txt")
    print(f"notices: {len(bundled_distributions())} distributions"
          + (f"; no licence file in: {', '.join(missing)}" if missing else ""))
    if SELF_TEST.exists():
        shutil.rmtree(SELF_TEST)
    r = subprocess.run([str(APP / "HotspotLV.exe"), "--self-test", str(SELF_TEST)], timeout=900)
    print((SELF_TEST / "self-test.txt").read_text(encoding="utf-8"))
    if r.returncode != 0:
        raise SystemExit(f"the frozen self-test failed (exit {r.returncode:#x}) — no zip made")
    zip_path = DIST / f"HotspotLV-{version()}-win64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            z.write(f, Path("HotspotLV") / f.relative_to(APP))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (zip_path.with_suffix(".zip.sha256")).write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    print(f"{zip_path.name}: {zip_path.stat().st_size / 1e6:.0f} MB, SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
