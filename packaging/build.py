"""Build the Windows release of Hotspot LV, check it, and zip it.

    pip install -e ".[app,build]"
    python packaging/build.py                 → dist/HotspotLV/ and dist/HotspotLV-<version>-win64.zip (+ .sha256)

Steps (each one stops the build if it fails; a failed build leaves no zip behind):
  1. PyInstaller with packaging/hotspot-lv.spec (one folder, windowed, icon, version resource)
  2. every bundled binary must come from Python, site-packages or this project — never from whatever happens to be
     on the build machine's PATH (packaging review: Git for Windows' OpenSSL ended up in the zip)
  3. next to HotspotLV.exe: README.txt (first run), LICENSE.txt (MIT, this program), the LICENSES folder
     (LGPL-3.0, GPL-3.0, GPL-2.0 from Qt 6.12.0's source tree) and THIRD-PARTY-NOTICES.txt: the data licences;
     Python's licence and its acknowledgements for incorporated software; Qt for Python / Qt with exact source
     links and Qt's own third-party attributions (packaging/qt-attributions, fetched once by
     fetch_qt_attributions.py); PyInstaller; every bundled Python distribution with its licence files; PYPOWER
     (PSERC). A bundled module nobody owns stops the build.
  4. the frozen program checks itself, offline and with an empty cache, exactly as on a clean PC:
     HotspotLV.exe --self-test build/self-test   (exit code 0 = every step passed)
  5. the zip and its SHA-256 (LF line end: `sha256sum -c` and Get-FileHash both read it)
"""

from __future__ import annotations

import ast
import hashlib
import html
import importlib.metadata as md
import json
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
APP = DIST / "HotspotLV"
WORK = ROOT / "build" / "hotspot-lv"
SELF_TEST = ROOT / "build" / "self-test"
QT_ATTR = PACKAGING / "qt-attributions"
QT_VERSION = "6.12.0"
QT_LICENCES = ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "GPL-2.0-only.txt")   # copied to the LICENSES folder
LICENCE_STEMS = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
TEXT_SUFFIXES = ("", ".txt", ".md", ".rst", ".apache", ".bsd", ".mit", ".lgpl", ".gpl")
RULE = "-" * 78


def version() -> str:
    scope: dict = {}
    exec((ROOT / "src" / "hotspotlv" / "__init__.py").read_text(encoding="utf-8"), scope)
    return scope["__version__"]


# ---------------------------------------------------------------------------------------------- what is bundled
def toc(name: str) -> list:
    """A PyInstaller table of contents: ('TYPE' or 'name', [entries]) or a list of entries."""
    data = ast.literal_eval((WORK / name).read_text(encoding="utf-8"))
    return data[1] if isinstance(data, tuple) else data


def check_binary_origins() -> None:
    allowed = [Path(sys.base_prefix).resolve(), ROOT.resolve()]
    allowed += [Path(p).resolve() for p in site.getsitepackages() + [site.getusersitepackages()]]
    analysis = ast.literal_eval((WORK / "Analysis-00.toc").read_text(encoding="utf-8"))
    binaries = next(part for part in analysis if isinstance(part, list) and part and len(part[0]) == 3
                    and part[0][2] in ("BINARY", "EXTENSION"))
    shipped = {p.relative_to(APP / "_internal").as_posix().lower() for p in (APP / "_internal").rglob("*") if p.is_file()}
    strays = []
    for dest, src, kind in binaries:
        if dest.replace("\\", "/").lower() not in shipped:
            continue                                      # filtered out by the spec
        source = Path(src).resolve()
        if not any(source.is_relative_to(a) for a in allowed):
            strays.append(f"{dest} ← {source}")
    if strays:
        raise SystemExit("binaries from outside Python / site-packages / the project:\n  " + "\n  ".join(strays))
    print(f"binary origins: {len(binaries)} checked, all from Python, site-packages or the project")


def bundled_top_names() -> set[str]:
    internal = APP / "_internal"
    names = {p.name.split(".")[0] for p in internal.iterdir()
             if not p.name.endswith((".dist-info", ".egg-info"))}           # metadata folders, not modules
    names |= {entry[0].split(".")[0] for entry in toc("PYZ-00.toc")}
    return names


def bundled_distributions() -> list[md.Distribution]:
    """Every installed distribution owning a top-level module of the build; stops the build on an unowned one."""
    names = bundled_top_names()
    owners = md.packages_distributions()
    found: dict[str, md.Distribution] = {}
    unowned = []
    for top in sorted(names):
        dists = owners.get(top, [])
        for dist_name in dists:
            try:
                d = md.distribution(dist_name)
            except md.PackageNotFoundError:
                continue
            found[d.metadata["Name"].lower()] = d
        if not dists and top not in sys.stdlib_module_names and not _ours_or_runtime(top):
            unowned.append(top)
    if unowned:
        raise SystemExit(f"bundled modules with no owning distribution (no notice possible): {', '.join(unowned)}")
    found.pop("hotspot-lv", None)                       # this program: its own LICENSE sits next to the exe
    return sorted(found.values(), key=lambda d: d.metadata["Name"].lower())


def _ours_or_runtime(top: str) -> bool:
    """The program itself, PyInstaller's own pieces, and the Python / MSVC runtime files (noticed separately)."""
    low = top.lower()
    return (low in ("hotspotlv", "hotspot_lv", "base_library", "pyi_splash", "_internal")
            or low.startswith(("pyimod", "pyi_rth", "_pyi", "python3", "vcruntime", "msvcp", "ucrtbase", "api-ms-",
                               "libcrypto-3", "libssl-3", "libffi", "sqlite3", "_tk", "tcl", "concrt", "_bz2", "_lzma",
                               "_ctypes", "_decimal", "_hashlib", "_ssl", "_socket", "_queue", "_overlapped", "_asyncio",
                               "_multiprocessing", "_elementtree", "_uuid", "_wmi", "_zoneinfo", "_zstd", "pyexpat",
                               "select", "unicodedata", "_sqlite3", "_cffi")))


def licence_files(d: md.Distribution) -> list[tuple[str, str]]:
    out = []
    for f in d.files or []:
        stem = f.name.upper().split(".")[0]
        in_folder = any(part.lower() in ("licenses", "licences") for part in f.parts[:-1])
        if (in_folder or stem.startswith(LICENCE_STEMS)) and (in_folder or f.suffix.lower() in TEXT_SUFFIXES) \
                and f.suffix.lower() not in (".py", ".pyc", ".pyi", ".so", ".pyd", ".dll"):
            try:
                out.append((f.name, Path(d.locate_file(f)).read_text(encoding="utf-8", errors="replace")))
            except (OSError, UnicodeDecodeError):
                pass
    return out


# ---------------------------------------------------------------------------------------------- the notices
DATA_NOTE = """\
DATA (in _internal\\hotspotlv\\data — NOT under the MIT licence of the program)
  - SMHI open data, station 96190 Eskilstuna A, air temperature 2023 (weather_vasteras_2023.csv, column temp_c):
    Source: SMHI. Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/). Processed by
    Basel Haijar: re-indexed to whole UTC hours, gaps of up to 6 h linearly interpolated.
  - PVGIS 5.3 hourly PV output 2023 (same file, column pv_kw_per_kwp): PVGIS © European Communities, 2001–2026;
    European Commission Joint Research Centre, free use with citation. Processed: stamps floored to the hour.
  - Norwegian household electricity demand (base_load_norway_2021.*): Hofmann, M., Bjarghov, S., Nessa, S. (2023),
    Zenodo, https://doi.org/10.5281/zenodo.8423312 — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
    Processed: a 63-house subset, renamed, gaps filled; also the base loads of the synthetic example stations.
  - EV charging histograms (ev_*_hist.csv, reference only): Sørensen, Å. L. (2024), Zenodo,
    https://doi.org/10.5281/zenodo.12730566 — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
    Processed: aggregated to histograms.
  Full table with citations: SOURCES.md (the program's "Assumptions & sources" tab).
"""


def python_notice() -> list[str]:
    lic = Path(sys.base_prefix) / "LICENSE.txt"
    page = Path(sys.base_prefix) / "Doc" / "html" / "license.html"
    lines = [RULE, f"Python {sys.version.split()[0]} (the runtime in _internal: python3*.dll, DLLs, the standard library)",
             "  Licence: Python Software Foundation License Version 2 (PSF-2.0) — retained in full below.", ""]
    lines.append(lic.read_text(encoding="utf-8", errors="replace") if lic.exists()
                 else "  (LICENSE.txt not found in the build's Python — see https://docs.python.org/3/license.html)")
    if page.exists():
        text = page.read_text(encoding="utf-8")
        start = text.find("Licenses and Acknowledgements for Incorporated Software")
        end = text.find("Previous topic", start)
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text[start:end if end > 0 else None], flags=re.S)
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("¶", "")
        body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body).strip()
        lines += ["", "Python — Licenses and Acknowledgements for Incorporated Software (OpenSSL, expat, libffi, zlib,",
                  "libmpdec, mimalloc, Zstandard and others), from the Python documentation of this version:", "", body]
    return lines


def qt_notice() -> list[str]:
    lines = [RULE, f"Qt for Python (PySide6, shiboken6) {QT_VERSION} and Qt {QT_VERSION}",
             "  Licence: GNU Lesser General Public License v3 (LGPL-3.0-only); the GPL v3 and GPL v2 it refers to are",
             "  included. The texts are in the LICENSES folder next to HotspotLV.exe (from Qt's source tree, tag",
             f"  v{QT_VERSION}). Copyright (C) The Qt Company Ltd. and other contributors.",
             "  Used unmodified. The Qt libraries are separate DLLs in _internal\\PySide6 and the PySide6 / shiboken6",
             "  Python files are plain files in _internal — each can be replaced by another build of the same version.",
             "  Source code of exactly this version:",
             f"    Qt for Python: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{QT_VERSION}-src/",
             f"    Qt:            https://download.qt.io/official_releases/qt/{QT_VERSION.rsplit('.', 1)[0]}/{QT_VERSION}/submodules/",
             "", "  Third-party code inside the Qt libraries (Qt's own attributions, qt_attribution.json of qtbase and",
             f"  qtimageformats {QT_VERSION}; some entries apply only to other platforms):", ""]
    from fetch_qt_attributions import license_files                   # one rule for fetching and for reading
    texts: dict[str, tuple[str, str]] = {}
    for j in sorted(QT_ATTR.rglob("qt_attribution.json")):
        module = j.relative_to(QT_ATTR).parts[0]
        if j.relative_to(QT_ATTR / module).parts[0] == "tests":          # Qt's test data, not in the libraries
            continue
        data = json.loads(j.read_text(encoding="utf-8"), strict=False)
        for e in (data if isinstance(data, list) else [data]):
            refs = []
            for src in license_files(e, j.parent.relative_to(QT_ATTR / module)):
                p = QT_ATTR / module / src
                if not p.is_file():
                    raise SystemExit(f"Qt attribution {e.get('Name')}: licence text {module}/{src} missing — "
                                     "run packaging/fetch_qt_attributions.py")
                body = p.read_text(encoding="utf-8", errors="replace")
                key = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
                texts.setdefault(key, (p.name, body))
                refs.append(f"[Q-{key}]")
            copyright_ = e.get("Copyright", "")
            if isinstance(copyright_, list):
                copyright_ = "; ".join(copyright_)
            lines.append(f"  * {e.get('Name', '?')} {e.get('Version', '')}".rstrip()
                         + f" — {e.get('License', e.get('LicenseId', ''))} {' '.join(refs)}")
            if copyright_:
                lines.append("      " + " ".join(copyright_.split()))
    lines += ["", "  Licence texts referred to above:"]
    for key, (name, body) in texts.items():
        lines += ["", f"  [Q-{key}] {name}", body]
    return lines


MSVC_NOTE = """\
Microsoft Visual C++ runtime (vcruntime140*.dll and msvcp140*.dll in _internal, and the copies that numpy, pandas,
PySide6 and shiboken6 bring along)
  Microsoft Distributable Code, redistributed unmodified with the Python and library builds that need it. The
  conditions are those of Python's "Additional Conditions for this Windows binary build" above; Microsoft's
  terms: https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution
"""

PYINSTALLER_NOTE = """\
PyInstaller (the bootloader in HotspotLV.exe and its run-time modules)
  The bootloader is GPL-2.0-or-later with the PyInstaller bootloader exception (programs built with it may be
  distributed under any licence); the run-time hooks and modules are Apache-2.0. Text: below.
"""

PYPOWER_NOTE = """\
PYPOWER / MATPOWER code inside pandapower (pandapower/pypower)
  Copyright (c) 1996-2015 PSERC. All rights reserved. Use of this source code is governed by a BSD-style license:
  Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
  following conditions are met: 1. Redistributions of source code must retain the above copyright notice, this list of
  conditions and the following disclaimer. 2. Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided
  with the distribution. 3. Neither the name of the copyright holder nor the names of its contributors may be used to
  endorse or promote products derived from this software without specific prior written permission.
  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
  INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
  WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


def write_notices(path: Path) -> list[str]:
    lines = [f"Hotspot LV {version()} — third-party software and data in this Windows build", "=" * 78, "",
             "Hotspot LV itself is MIT-licensed (LICENSE.txt). This build also contains the data, the Python runtime and",
             "the libraries below, each under its own licence, all used unmodified. Every library's source is linked.",
             "", DATA_NOTE]
    lines += python_notice() + ["", RULE, MSVC_NOTE] + qt_notice() + ["", RULE, PYINSTALLER_NOTE]
    try:
        lines += [text for _, text in licence_files(md.distribution("pyinstaller"))]
    except md.PackageNotFoundError:
        lines.append("  (PyInstaller's licence text: https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt)")
    lines += ["", RULE, PYPOWER_NOTE]
    missing = []
    for d in bundled_distributions():
        meta = d.metadata
        licence = meta.get("License-Expression") or meta.get("License") or ""
        if "\n" in licence or len(licence) > 160:            # the whole text sits in the field: shown with the files
            licence = "see the licence text below"
        classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        files = licence_files(d)
        if not files:
            missing.append(meta["Name"])
        lines += [RULE, f"{meta['Name']} {meta['Version']}",
                  f"  Licence: {licence or ', '.join(classifiers) or 'see the project'}",
                  f"  Source (used unmodified): https://pypi.org/project/{meta['Name']}/{meta['Version']}/", ""]
        lines += [f"--- {name} ---\n{text}" for name, text in files] or \
                 ["  (no licence file in the installed distribution — see the project page above)"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return missing


# ---------------------------------------------------------------------------------------------- the build
def run(cmd: list[str], **kw) -> None:
    print("»", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")              # a redirected log in the console code page garbles
    sys.stderr.reconfigure(encoding="utf-8")              # "—" and could fail on a FAIL line (review S-e)
    zip_path = DIST / f"HotspotLV-{version()}-win64.zip"
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    for old in (zip_path, sha_path):                     # a failed build must not leave the previous zip around
        old.unlink(missing_ok=True)
    run([sys.executable, "-m", "PyInstaller", str(PACKAGING / "hotspot-lv.spec"), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")], cwd=ROOT)
    check_binary_origins()
    (APP / "LICENSE.txt").write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8",
                                     newline="\r\n")       # the same bytes from any checkout (review S-d)
    (APP / "README.txt").write_text((PACKAGING / "README-windows.txt").read_text(encoding="utf-8")
                                    .replace("{version}", version()), encoding="utf-8", newline="\r\n")
    licences = APP / "LICENSES"
    licences.mkdir(exist_ok=True)
    for name in QT_LICENCES:                               # the Qt licence and the two it refers to; the other
        shutil.copy2(QT_ATTR / "qtbase" / "LICENSES" / name, licences / name)   # texts are in the notices
    missing = write_notices(APP / "THIRD-PARTY-NOTICES.txt")
    print(f"notices: {len(bundled_distributions())} distributions"
          + (f"; no licence file in: {', '.join(missing)}" if missing else ""))
    if missing:
        raise SystemExit("a bundled distribution has no licence file — add its text before releasing")

    if SELF_TEST.exists():
        shutil.rmtree(SELF_TEST)
    with tempfile.TemporaryDirectory(prefix="hotspotlv-cache-") as empty_cache:   # a clean PC: no cache, no network
        env = {**os.environ, "HOTSPOTLV_CACHE": empty_cache, "HOTSPOTLV_OFFLINE": "1"}
        try:
            r = subprocess.run([str(APP / "HotspotLV.exe"), "--self-test", str(SELF_TEST)], timeout=1200, env=env)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = "timeout after 1200 s"
    log = SELF_TEST / "self-test.txt"
    print(log.read_text(encoding="utf-8") if log.exists() else "(the self-test wrote no log)")
    if code != 0:
        raise SystemExit(f"the frozen self-test failed ({code if isinstance(code, str) else f'exit {code:#x}'}) — no zip made")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            z.write(f, Path("HotspotLV") / f.relative_to(APP))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path.write_bytes(f"{digest}  {zip_path.name}\n".encode("ascii"))
    size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
    print(f"{zip_path.name}: {zip_path.stat().st_size / 1e6:.0f} MB zipped, {size / 1e6:.0f} MB unzipped, SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
