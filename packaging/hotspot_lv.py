"""PyInstaller entry point: the same `main()` as `python -m hotspotlv.app` and the `hotspot-lv` command."""

import sys

from hotspotlv.app.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
