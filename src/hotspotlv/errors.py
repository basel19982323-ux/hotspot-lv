"""Error text for a planner: the sentence, without Python's class name, for the errors the program expects (bad
input, a download that failed, a missing or locked file). Anything else — a programming error — keeps its class
name, so it is recognisable as a fault of the program and not of the user's file (packaging reviews of 22 Oct 2026:
"ConnectionError: …" and "FileNotFoundError: [Errno 2] …" reached the planner as they were)."""

from __future__ import annotations

import re

EXPECTED = ("ValueError", "ConnectionError", "SmhiNotFound", "FileNotFoundError", "PermissionError", "OSError",
            "IsADirectoryError", "NotADirectoryError", "UnicodeDecodeError")


def plain(text: str) -> str:
    """The first line of an error text, without the class name of an expected error and without '[Errno 2] '."""
    first = str(text).split("\n")[0]
    m = re.match(r"(\w+): (.*)", first)
    if m and m.group(1) in EXPECTED:
        first = re.sub(r"^\[(?:Errno|WinError) \d+\] ", "", m.group(2))
    return first


def of(e: BaseException) -> str:
    """plain() of an exception."""
    return plain(f"{type(e).__name__}: {e}")
