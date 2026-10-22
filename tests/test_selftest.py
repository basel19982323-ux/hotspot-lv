"""`hotspot-lv --self-test <folder>`: the end-to-end check a frozen build runs on itself. Here from the source tree, so
the suite fails when the self-test itself breaks (it is the only test a packaged build gets)."""

import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")


def test_self_test_option_is_parsed_and_removed_from_argv(tmp_path):
    from hotspotlv.app.__main__ import _self_test_folder
    folder, argv = _self_test_folder(["HotspotLV.exe", "--self-test", str(tmp_path), "-platform", "windows"])
    assert folder == tmp_path.resolve() and argv == ["HotspotLV.exe", "-platform", "windows"]
    assert _self_test_folder(["HotspotLV.exe"]) == (None, ["HotspotLV.exe"])
    with pytest.raises(SystemExit):
        _self_test_folder(["HotspotLV.exe", "--self-test"])


def test_the_self_test_runs_end_to_end_and_exits_cleanly(tmp_path):
    """Station, PDF, fleet, a threaded scenario run, a cancelled full run, and the close over a running assessment —
    the real entry point, exit code 0, every step written down."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    r = subprocess.run([sys.executable, "-m", "hotspotlv.app", "--self-test", str(tmp_path)],
                       capture_output=True, text=True, timeout=600, env=env)
    log = (tmp_path / "self-test.txt").read_text(encoding="utf-8")
    assert r.returncode == 0, f"exit {r.returncode:#x}\n{log}\n{r.stderr[-1500:]}"
    for step in ("station", "pdf", "fleet", "scenario", "cancel"):
        assert f"PASS {step}:" in log, log
    assert "close:" in log and log.rstrip().splitlines()[-1].startswith("ALL PASSED")
    assert (tmp_path / "station_card.pdf").stat().st_size > 100_000
    assert all((tmp_path / f).exists() for f in ("station.png", "fleet.png", "scenario.png"))
