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
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]       # absolute, not resolve(): a short drive mapping (subst) stays short
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
ACK_MARKERS = ("Mersenne Twister", "Thai Open Source", "Stefan Krah", "mimalloc")   # expat, libmpdec: texts present
MIN_OPENSSL = (3, 5)                # the 3.0 line is out of support; Python 3.14.6+ for Windows bundles 3.5
QT_SOURCE = "https://download.qt.io/archive/qt/{minor}/{v}/submodules/"            # archive/: kept for every version
QT_MODULES = ("qtbase", "qtsvg", "qtimageformats")
# Qt attributions that describe code not in this Windows build: other platforms, build tools, Qt modules that are not
# shipped (round-2 review: GPL-2.0, LGPL-2.1 and BSD-4 entries that do not apply, with nothing saying so)
QT_NOT_IN_BUILD_IDS = {"extra-cmake-modules", "kwin", "cmake-runcmake-test-modules", "android-native-style",
                       "android-gradle-wrapper", "forkfd", "pixman", "sqlite", "dejayvu", "xcb-xinput",
                       "qeventdispatcher_cf", "libdbus-1-headers", "cocoa-platform-plugin", "linuxperf", "valgrind",
                       "catch2"}            # catch2: Qt's tests of Qt Test, not compiled into Qt6Test.dll (round 3)
HACL_NOTE = """\
HACL* (in python314.dll: the hash functions of hashlib — Python's own notices do not list it)
  MIT License. Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation; Copyright (c) 2022-2023 HACL* Contributors.
  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
  documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
  persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice
  and this permission notice shall be included in all copies or substantial portions of the Software.
  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
  WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
  COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
  OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
  Source: https://github.com/python/cpython/tree/v{python}/Modules/_hacl
"""
QT_NETWORK_IDS = {"psl-data", "libpsl"}                    # in Qt Network, which this build does not ship


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
    Basel Haijar: the hours 2022-12-31 23:00 – 2023-12-31 23:00 UTC (the Swedish calendar year 2023 and the
    last UTC hour), gaps of up to 6 h linearly interpolated.
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
    if not page.exists():
        raise SystemExit(f"{page} is missing: the notices need Python's acknowledgements for incorporated software")
    text = page.read_text(encoding="utf-8")
    # the section itself, not the sidebar's table of contents with the same heading (round-2 review: the notices
    # held only the headings): from <section id="otherlicenses"> to the sidebar after the page body
    start = text.rfind("<section", 0, text.find('id="otherlicenses"'))
    end = text.rfind("<div", start, text.find('class="sphinxsidebar"', start))   # before the tag, not inside it
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text[start:end], flags=re.S)
    body = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("¶", "")
    body = re.sub(r"\n\s*\n\s*\n+", "\n\n", body).strip()
    if start < 0 or end < 0 or len(body) < 20_000 or not all(k in body for k in ACK_MARKERS):
        raise SystemExit(f"Python's acknowledgements were not found in {page} (got {len(body)} characters) — "
                         "check python_notice() against this Python version's license.html")
    lines += ["", "Python — Licenses and Acknowledgements for Incorporated Software (OpenSSL, expat, libffi, zlib,",
              "libmpdec, mimalloc, Zstandard and others), from the Python documentation of this version:", "", body,
              "", HACL_NOTE.format(python=sys.version.split()[0])]
    return lines


def _freetype_year() -> str:
    """The FreeType licence asks for the year of the FreeType version used: the newest year in Qt's attribution."""
    data = json.loads((QT_ATTR / "qtbase" / "src" / "3rdparty" / "freetype" / "qt_attribution.json")
                      .read_text(encoding="utf-8"), strict=False)
    entry = next(e for e in (data if isinstance(data, list) else [data]) if e.get("Id") == "freetype")
    text = entry.get("Copyright", "")
    text = "; ".join(text) if isinstance(text, list) else text
    return max(re.findall(r"(?:19|20)\d\d", text))


def qt_notice() -> list[str]:
    lines = [RULE, f"Qt for Python (PySide6, shiboken6) {QT_VERSION} and Qt {QT_VERSION}",
             "  Licence: GNU Lesser General Public License v3 (LGPL-3.0-only); the GPL v3 and GPL v2 it refers to are",
             "  included. The texts are in the LICENSES folder next to HotspotLV.exe (from Qt's source tree, tag",
             f"  v{QT_VERSION}). Copyright (C) The Qt Company Ltd. and other contributors.",
             "  Used unmodified. The Qt libraries are separate DLLs in _internal\\PySide6 and the PySide6 / shiboken6",
             "  Python files are plain files in _internal — each can be replaced by another build of the same version.",
             "  Source code of exactly this version:",
             f"    Qt for Python: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{QT_VERSION}-src/"
             f" (pyside-setup-everywhere-src-{QT_VERSION}.tar.xz)",
             f"    Qt:            {QT_SOURCE.format(minor=QT_VERSION.rsplit('.', 1)[0], v=QT_VERSION)}",
             "                   (" + ", ".join(f"{m}-everywhere-src-{QT_VERSION}.tar.xz" for m in QT_MODULES) + ")",
             "", "  This software is based in part on the work of the Independent JPEG Group.",
             f"  Portions of this software are copyright (c) {_freetype_year()} The FreeType Project (www.freetype.org). "
             "All rights reserved.",
             "", "  Third-party code inside Qt and Qt for Python (their own attributions, qt_attribution.json of qtbase,",
             f"  qtimageformats and pyside-setup {QT_VERSION}; qtsvg has none). First the parts in this Windows build,",
             "  then those the source trees also list but this build does not contain (other platforms, build tools,",
             "  Qt modules that are not shipped) — kept for completeness, they do not apply here.", ""]
    from fetch_qt_attributions import license_files                   # one rule for fetching and for reading
    network_shipped = (APP / "_internal" / "PySide6" / "Qt6Network.dll").exists()
    not_in_build = QT_NOT_IN_BUILD_IDS | (set() if network_shipped else QT_NETWORK_IDS)
    texts: dict[str, tuple[str, str]] = {}
    entries: dict[bool, list[str]] = {True: [], False: []}
    for j in sorted(QT_ATTR.rglob("qt_attribution.json")):
        module = j.relative_to(QT_ATTR).parts[0]
        if j.relative_to(QT_ATTR / module).parts[0] in ("tests", "examples"):   # not in the libraries
            continue
        data = json.loads(j.read_text(encoding="utf-8"), strict=False)
        for e in (data if isinstance(data, list) else [data]):
            in_build = e.get("Id") not in not_in_build and "/wayland/" not in j.as_posix()
            lines_before = len(lines)
            refs = []
            for src in license_files(e, j.parent.relative_to(QT_ATTR / module)):
                p = QT_ATTR / module / src
                if not p.is_file():
                    raise SystemExit(f"Qt attribution {e.get('Name')}: licence text {module}/{src} missing — "
                                     "run packaging/fetch_qt_attributions.py")
                body = p.read_text(encoding="utf-8", errors="replace")
                if p.suffix in (".h", ".c", ".cpp") and "/*" in body:   # a source file: its licence comment only
                    body = body[body.index("/*") + 2:body.index("*/", body.index("/*"))].strip("\n")
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
            entries[in_build] += lines[lines_before:]                 # moved into the two lists below
            del lines[lines_before:]
    lines += ["  In this build:"] + entries[True]
    lines += ["", "  Not in this Windows build (listed by Qt's source tree only):"] + entries[False]
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
  These files are Microsoft's: they may not be modified, and they are provided only to run this program.
"""

PYINSTALLER_NOTE = """\
PyInstaller (the bootloader in HotspotLV.exe and its run-time modules) and pyinstaller-hooks-contrib (run-time hooks)
  The bootloader is GPL-2.0-or-later with the PyInstaller bootloader exception (programs built with it may be
  distributed under any licence); the run-time hooks and modules are Apache-2.0, (c) PyInstaller Development Team,
  including the hooks of pyinstaller-hooks-contrib {hooks}. Source: https://github.com/pyinstaller/pyinstaller and
  https://github.com/pyinstaller/pyinstaller-hooks-contrib. Text: below.
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


def write_notices(path: Path, commit: str, test_build: bool = False) -> list[str]:
    lines = [f"Hotspot LV {version()} — third-party software and data in this Windows build", "=" * 78, "",
             f"Built from commit {commit} of the Hotspot LV source"
             + (" — TEST BUILD, not a release (see README.txt)." if test_build else "."),
             "Hotspot LV itself is MIT-licensed (LICENSE.txt). This build also contains the data, the Python runtime and",
             "the libraries below, each under its own licence. The libraries are used unmodified; the data are",
             "processed as stated. Source links are given for Python, Qt and every open-source library.",
             "", DATA_NOTE]
    try:
        hooks = md.version("pyinstaller-hooks-contrib")
    except md.PackageNotFoundError:
        hooks = "(not installed)"
    lines += (python_notice() + ["  Source: https://www.python.org/downloads/source/", "", RULE, MSVC_NOTE]
              + qt_notice() + ["", RULE, PYINSTALLER_NOTE.format(hooks=hooks)])
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
        if meta["Name"].lower().startswith(("pyside6", "shiboken6")):   # the wheel ships Qt's commercial text too
            lines += ["  This build uses these files under LGPL-3.0-only — see the Qt for Python section above and",
                      "  the LICENSES folder next to HotspotLV.exe.", ""]
        lines += [f"--- {name} ---\n{text}" for name, text in files] or \
                 ["  (no licence file in the installed distribution — see the project page above)"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return missing


# ---------------------------------------------------------------------------------------------- the build
def run(cmd: list[str], **kw) -> None:
    print("»", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def git(*args: str) -> str:
    """git's answer, or None outside a git checkout (round-3 review: a bare traceback, even with --test-build)."""
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def check_folder_limit() -> None:
    """Every DLL and extension module must load from a program folder of FOLDER_LIMIT characters: the deepest one
    sets the real limit, and a library update could move it (round-3 review: the limit was not tied to the bundle)."""
    sys.path.insert(0, str(ROOT / "src"))
    from hotspotlv.app.__main__ import FOLDER_LIMIT, MAX_PATH
    deepest = max((p for p in APP.rglob("*") if p.suffix.lower() in (".pyd", ".dll")),
                  key=lambda p: len(str(p.relative_to(APP))))
    depth = len(str(deepest.relative_to(APP)))
    real = MAX_PATH - 1 - depth
    if FOLDER_LIMIT > real:
        raise SystemExit(f"{deepest.relative_to(APP)} lies {depth} characters below the program folder: it loads only "
                         f"from folders up to {real} characters, but FOLDER_LIMIT is {FOLDER_LIMIT} — lower it")
    print(f"folder limit: {FOLDER_LIMIT} characters allowed, {real} would load "
          f"(deepest: {deepest.relative_to(APP)}, {depth} characters)")


def project_url() -> str:
    import tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["urls"]["Source"]


def release_problems() -> list[str]:
    """What makes this build unfit for publishing: an unsupported OpenSSL in the build's Python, uncommitted changes
    (the zip would match no commit), files checked out with CRLF against .gitattributes (round-2 review: the zip
    could not be rebuilt byte for byte from its commit)."""
    import ssl
    problems = []
    if ssl.OPENSSL_VERSION_INFO[:2] < MIN_OPENSSL:
        problems.append(f"{ssl.OPENSSL_VERSION} (Python {sys.version.split()[0]}) is out of support — build with "
                        "Python 3.14.8 or later, which bundles OpenSSL 3.5")
    status = git("status", "--porcelain")
    if status is None:
        return problems + ["this is not a git checkout (or git is missing) — a release must come from a commit"]
    if status:
        problems.append("the working tree has uncommitted changes — commit first, so the zip matches its commit")
    crlf = [line.split("\t")[-1] for line in (git("ls-files", "--eol") or "").splitlines()
            if "w/crlf" in line and "eol=lf" in line]
    if crlf:
        problems.append(f"{len(crlf)} files are checked out with CRLF although .gitattributes says LF (e.g. {crlf[0]})"
                        " — delete them and check them out again")
    return problems


def visible_windows(pid: int) -> list[str]:
    """Title and texts of every visible top-level window of the process — a self-test shows none, so any window is
    a modal box nobody will click (the bootloader's "Unhandled exception", Qt's "no platform plugin")."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: list[str] = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def text_of(hwnd) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            texts = [text_of(hwnd)]

            def child(h, _):                               # the box's own text sits in a child window
                texts.append(text_of(h))
                return True
            user32.EnumChildWindows(hwnd, callback(child), 0)
            found.append(" | ".join(t for t in texts if t))
        return True
    user32.EnumWindows(callback(each), 0)
    return found


def run_self_test(env: dict, limit_s: int = 1200) -> int | str:
    """The frozen self-test; a window on the screen or the time limit stops it, and says which."""
    proc = subprocess.Popen([str(APP / "HotspotLV.exe"), "--self-test", str(SELF_TEST)], env=env)
    t0 = time.monotonic()
    while proc.poll() is None:
        windows = visible_windows(proc.pid)
        if windows:
            proc.kill()
            proc.wait()
            return f"a window appeared and would have waited for a click: {windows}"
        if time.monotonic() - t0 > limit_s:
            proc.kill()
            proc.wait()
            return f"timeout after {limit_s} s"
        time.sleep(0.5)
    return proc.returncode


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")              # a redirected log in the console code page garbles
    sys.stderr.reconfigure(encoding="utf-8")              # "—" and could fail on a FAIL line (packaging review)
    test_build = "--test-build" in sys.argv[1:]
    problems = release_problems()
    if problems and not test_build:
        raise SystemExit("not fit for a release:\n  - " + "\n  - ".join(problems)
                         + "\n(python packaging/build.py --test-build makes a zip named …-TEST.zip anyway)")
    for p in problems:
        print(f"TEST BUILD ONLY: {p}")
    head = git("rev-parse", "--short=12", "HEAD")
    commit = (head or "unknown (not a git checkout)") + (" + uncommitted changes" if git("status", "--porcelain") else "")
    release_zip = DIST / f"HotspotLV-{version()}-win64.zip"
    zip_path = release_zip.with_name(release_zip.stem + "-TEST.zip") if test_build else release_zip
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    stale = [zip_path, sha_path]                         # a failed build must not leave a previous zip of its kind;
    if not test_build:                                   # a release also clears an old TEST zip, which could be taken
        test_zip = release_zip.with_name(release_zip.stem + "-TEST.zip")   # for it — a TEST build keeps the release
        stale += [test_zip, test_zip.with_name(test_zip.name + ".sha256")]  # (round-3 review)
    for old in stale:
        old.unlink(missing_ok=True)
    run([sys.executable, "-m", "PyInstaller", str(PACKAGING / "hotspot-lv.spec"), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build")], cwd=ROOT)
    check_binary_origins()
    check_folder_limit()
    (APP / "LICENSE.txt").write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8",
                                     newline="\r\n")       # the same bytes from any checkout (packaging review)
    readme = (PACKAGING / "README-windows.txt").read_text(encoding="utf-8")
    test_note = ("TEST BUILD — not a release (made with --test-build"
                 + (", and not fit for publishing because:\n" + "".join(f"  - {p}\n" for p in problems)
                    if problems else ").\n") + "\n") if test_build else ""
    readme = (readme.replace("{version}", version()).replace("{url}", project_url()).replace("{commit}", commit)
              .replace("{zip}", zip_path.name).replace("{test_note}", test_note))
    (APP / "README.txt").write_text(readme, encoding="utf-8", newline="\r\n")
    licences = APP / "LICENSES"
    licences.mkdir(exist_ok=True)
    for name in QT_LICENCES:                               # the Qt licence and the two it refers to; the other
        src = QT_ATTR / "qtbase" / "LICENSES" / name       # texts are in the notices
        if not src.is_file():
            raise SystemExit(f"{src} is missing — run packaging/fetch_qt_attributions.py")
        shutil.copy2(src, licences / name)
    missing = write_notices(APP / "THIRD-PARTY-NOTICES.txt", commit, test_build)
    print(f"notices: {len(bundled_distributions())} distributions"
          + (f"; no licence file in: {', '.join(missing)}" if missing else ""))
    if missing:
        raise SystemExit("a bundled distribution has no licence file — add its text before releasing")

    if SELF_TEST.exists():
        shutil.rmtree(SELF_TEST)
    with tempfile.TemporaryDirectory(prefix="hotspotlv-cache-") as empty_cache:   # a clean PC: no cache, no network
        # nothing of this shell's Qt or Python settings: QT_QPA_PLATFORM=offscreen (how the tests run) let a
        # build without qwindows.dll pass (round-2 review)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("QT_", "PYTHON"))}
        env.update(HOTSPOTLV_CACHE=empty_cache, HOTSPOTLV_OFFLINE="1")
        code = run_self_test(env)
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
