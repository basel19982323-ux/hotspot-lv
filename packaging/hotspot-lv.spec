# PyInstaller spec for the Windows build of Hotspot LV — a one-folder app (HotspotLV\HotspotLV.exe + _internal\).
#
#   pip install -e ".[app,build]"
#   python -m PyInstaller packaging/hotspot-lv.spec --noconfirm --distpath dist --workpath build
#   dist\HotspotLV\HotspotLV.exe --self-test <folder>          (the frozen build checks itself; exit code 0 = passed)
#
# One folder, not one file: a one-file exe unpacks ~300 MB to a temp folder on every start (slow, and often flagged
# by antivirus); the folder starts at once and is zipped for a release.

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent                       # SPECPATH: the folder of this spec (packaging/)
VERSION = {}
exec((ROOT / "src" / "hotspotlv" / "__init__.py").read_text(encoding="utf-8"), VERSION)
version = VERSION["__version__"]
release = re.match(r"\d+(?:\.\d+){0,3}", version).group().split(".")   # "0.2.0rc1" → 0.2.0 (the resource takes numbers)
numbers = tuple(int(x) for x in release) + (0,) * (4 - len(release))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "Basel Haijar"),
        StringStruct("FileDescription", "Hotspot LV - secondary-substation transformer screening (simulation)"),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", "HotspotLV"),
        StringStruct("LegalCopyright", "Copyright (c) 2026 Basel Haijar - MIT licence"),
        StringStruct("OriginalFilename", "HotspotLV.exe"),
        StringStruct("ProductName", "Hotspot LV"),
        StringStruct("ProductVersion", version),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)

# The app's own data: weather 2023, base-load profiles, EV histograms, the example stations, SOURCES.md, the icon —
# read from THIS source tree, with the patterns of pyproject's package-data. (collect_data_files("hotspotlv") asked
# the installed package, and an editable install pointed a build from a clone at another tree's data: packaging
# review of 22 Oct 2026.)
# Deliberately NOT collected: pandapower's example networks (26 MB, the app builds its own feeder) and pvlib's data
# (30 MB, only the PVGIS download for other weather years uses pvlib, and the app does not offer that).
DATA = ROOT / "src" / "hotspotlv" / "data"
PATTERNS = ("*.csv", "*.csv.gz", "*.md", "*.png", "examples/*.csv", "examples/*.md")
datas = [(str(f), str(Path("hotspotlv") / "data" / f.parent.relative_to(DATA)))
         for pattern in PATTERNS for f in sorted(DATA.glob(pattern))]

a = Analysis(
    [str(ROOT / "packaging" / "hotspot_lv.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[],
    excludes=[
        "matplotlib", "tkinter", "IPython", "jupyter", "notebook", "pytest",   # not used by the package
        "PyQt5", "PyQt6", "PySide2",                                          # one Qt binding only
        "numba", "geopandas", "shapely", "plotly", "igraph",                  # pandapower extras the app does not use
        # (networkx is NOT excluded: pandapower imports it at load time — the frozen self-test caught that)
        "PySide6.QtNetwork",            # never loaded; its TLS plugin pulled a second OpenSSL from Git's PATH
        "h5py", "PIL",                  # optional imports of pandapower / pyqtgraph, never used (15 MB)
        "win32pdh", "pywintypes",       # numpy.testing only
        "setuptools", "pkg_resources", "_distutils_hack",   # never imported by the app; their hook added 1,100 lines
    ],                                                      # of notices (round-2 review)
    module_collection_mode={"PySide6": "py", "shiboken6": "py"},   # their LGPL Python files stay replaceable files
    noarchive=False,
)
# Files the app never loads (DLL census of the packaging review): software OpenGL (20 MB), Qt's translations
# (the UI is English), and anything of QtNetwork's plugins or OpenSSL that the Qt hook may still bring along;
# the TUIO touch plugin (touch input over the network) and the Qt6Network.dll it alone pulled in (round 2).
NEVER_LOADED = ("opengl32sw.dll", "/translations/", "/plugins/tls/", "/plugins/networkinformation/",
                "libssl-3-x64.dll", "libcrypto-3-x64.dll", "/plugins/generic/", "qt6network.dll")
a.binaries = [b for b in a.binaries if not any(k in b[0].replace("\\", "/").lower() for k in NEVER_LOADED)]
a.datas = [d for d in a.datas if not any(k in d[0].replace("\\", "/").lower() for k in NEVER_LOADED)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="HotspotLV",
    console=False,                                 # a windowed program: no console window behind the app
    icon=str(ROOT / "packaging" / "hotspot-lv.ico"),
    version=version_info,
    upx=False,                                     # UPX-packed files are a common antivirus false positive
)
coll = COLLECT(exe, a.binaries, a.datas, name="HotspotLV", upx=False)
