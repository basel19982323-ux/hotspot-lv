# Qt's third-party attribution files — not under this project's MIT licence

This folder holds copies, unchanged, of files from Qt's own source trees at tag v6.12.0 (qtbase, qtimageformats,
pyside-setup), fetched by `packaging/fetch_qt_attributions.py` (see `SOURCE.txt`). `packaging/build.py` turns them
into the Qt section of the Windows build's THIRD-PARTY-NOTICES.txt.

- **`qt_attribution.json` files**: Copyright (C) The Qt Company Ltd. Qt licenses them under
  `LicenseRef-Qt-Commercial OR GFDL-1.3-no-invariants-only` (Qt's `REUSE.toml`). They are redistributed here under
  the GNU Free Documentation License 1.3, no invariant sections — text in `GFDL-1.3-no-invariants-only.txt` in
  this folder.
- **Licence texts** (`LICENSES/*.txt`, `LICENSE*`, `COPYING*` and similar): the licences of the third-party code
  inside Qt, each under its own terms, copied as they are.
- **`pyside-setup/sources/shiboken6/libshiboken/bufferprocs_py37.h`**: a Qt for Python source file, Copyright (C)
  The Qt Company Ltd., `LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; used here only
  for the Python licence text in its header comment. LGPL-3.0 text: `qtbase/LICENSES/LGPL-3.0-only.txt`.

The MIT licence in the repository's `LICENSE` file covers Hotspot LV's own code, not the files in this folder.
