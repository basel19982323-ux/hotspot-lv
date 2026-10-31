"""What the planner reads, pinned (packaging review round 2): error sentences without Python class names, the
example card marked synthetic with its data credited, and the README / About / SOURCES naming the host the
program really downloads from, with SMHI's three-month lag."""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from hotspotlv import errors
from hotspotlv.paths import package_data
from hotspotlv.weather import SMHI_HOST

ROOT = Path(__file__).resolve().parents[1]


def test_expected_errors_lose_their_class_name_and_bugs_keep_it():
    assert errors.plain("ValueError: the file has no kW column") == "the file has no kW column"
    assert errors.plain("ConnectionError: Outdoor temperatures …\n\nTraceback …") == "Outdoor temperatures …"
    assert errors.of(FileNotFoundError(2, "No such file or directory", "base.csv")) == \
        "No such file or directory: 'base.csv'"
    assert errors.plain("SmhiNotFound: SMHI has no hourly air temperatures") == "SMHI has no hourly air temperatures"
    assert errors.plain("KeyError: 'verdict'") == "KeyError: 'verdict'"              # a fault of the program
    assert errors.plain("RuntimeError: odd") == "RuntimeError: odd"


def test_a_shipped_example_card_says_it_is_synthetic_and_credits_its_data():
    pytest.importorskip("PySide6")
    from hotspotlv.app.report import station_card_html
    from hotspotlv.station import ambient_for, assess_station, read_station_csv
    st = read_station_csv(package_data() / "examples" / "ns-102.csv")
    res = assess_station(st, 500, ambient_for(st.index))
    card = station_card_html(res)
    assert "<b>Synthetic example</b> — not a measured station" in card
    assert "Hofmann, Bjarghov &amp; Nessa (2023)" in card and "zenodo.8423312" in card
    assert "PVGIS © European Communities, 2001–2026" in card                         # round 3: its solar output
    assert "a synthetic example shipped with the program" in card


def test_a_copied_example_is_a_station_file_like_any_other(tmp_path):
    pytest.importorskip("PySide6")
    from hotspotlv.app.report import station_card_html
    from hotspotlv.station import ambient_for, assess_station, read_station_csv
    copy = tmp_path / "my-station.csv"
    shutil.copy(package_data() / "examples" / "ns-102.csv", copy)
    st = read_station_csv(copy)
    card = station_card_html(assess_station(st, 500, ambient_for(st.index)))
    assert "Synthetic example" not in card and "Hofmann" not in card


@pytest.mark.parametrize("path", ["packaging/README-windows.txt", "src/hotspotlv/data/SOURCES.md",
                                  "src/hotspotlv/app/main_window.py"])
def test_the_texts_name_the_real_download_host_and_the_lag(path):
    """Round 2: the texts said opendata.smhi.se and "downloads once"; the program downloads from
    opendata-download-metobs.smhi.se, and the latest months come separately."""
    text = (ROOT / path).read_text(encoding="utf-8")
    assert SMHI_HOST in text and "three months" in text and "latest-months" in text
    assert "downloads that station's hourly archive once" not in text and "archive once from" not in text


def test_the_version_is_one_number_everywhere_and_in_the_changelog():
    """Catches: pyproject and the program disagreeing on the version, or a version with no changelog entry."""
    import re
    import tomllib
    from hotspotlv import __version__
    root = Path(__file__).resolve().parents[1]
    assert tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == __version__
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## {re.escape(__version__)} — ", changelog, re.M)
