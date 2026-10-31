"""`python -m hotspotlv report <csv> --kva 500` — the report command (cli.py); with no command, the desktop app."""

import sys

from hotspotlv.app.__main__ import main

sys.exit(main())
