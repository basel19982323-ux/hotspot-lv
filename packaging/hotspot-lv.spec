# PyInstaller spec for the Windows build of Hotspot LV — a one-folder app (HotspotLV\HotspotLV.exe + _internal\).
#
#   pip install -e ".[app,build]"
#   python -m PyInstaller packaging/hotspot-lv.spec --noconfirm --distpath dist --workpath build
#   dist\HotspotLV\HotspotLV.exe --self-test <folder>          (the frozen build checks itself; exit code 0 = passed)
#
# One folder, not one file: a one-file exe unpacks ~300 MB to a temp folder on every start (slow, and often flagged
# by antivirus); the folder starts at once and is zipped for a release.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent                       # SPECPATH: the folder of this spec (packaging/)
VERSION = {}
exec((ROOT / "src" / "hotspotlv" / "__init__.py").read_text(encoding="utf-8"), VERSION)
version = VERSION["__version__"]
numbers = tuple(int(x) for x in version.split(".")) + (0,) * (4 - len(version.split(".")))

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

# The app's own data: weather 2023, base-load profiles, EV histograms, the example stations, SOURCES.md, the icon.
# Deliberately NOT collected: pandapower's example networks (26 MB, the app builds its own feeder) and pvlib's data
# (30 MB, only the PVGIS download for other weather years uses pvlib, and the app does not offer that).
datas = collect_data_files("hotspotlv")

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
    ],
    noarchive=False,
)
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
