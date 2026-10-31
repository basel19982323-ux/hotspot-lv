"""The one-command report: `hotspot-lv report <csv> --kva 500 --out report.pdf|.html`. Offscreen (no fonts): the
PDF's structure is checked with pypdf, its words through the HTML form of the same report."""

import os
import re
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from hotspotlv.app.report import INDICATIVE, REPORT_CHARTS, key_figures  # noqa: E402
from hotspotlv.cli import main, parser  # noqa: E402
from hotspotlv.paths import package_data  # noqa: E402
from hotspotlv.station import ambient_for, assess_station, read_station_csv  # noqa: E402
from hotspotlv.verdict import IEC_TABLE_4  # noqa: E402

EXAMPLE = package_data() / "examples" / "ns-102.csv"          # synthetic, 2023: SMHI temperatures ship offline


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def html_report(app, tmp_path_factory):
    out = tmp_path_factory.mktemp("cli") / "r.html"
    assert main([str(EXAMPLE), "--kva", "500", "--out", str(out)]) == 0
    return out.read_text(encoding="utf-8")


def test_the_html_report_has_every_part_asked_for(html_report):
    text = re.sub(r"<[^>]+>", " ", html_report)
    for words in ("Hotspot LV — station report", INDICATIVE, "Key figures", "What was read from the file",
                  "peak load vs rating", "hours above the IEC normal cyclic limits", "relative ageing rate",
                  "Temperature duration curve", "Loading duration curve", "verdict"):
        assert words in text, words
    assert html_report.count("src='data:image/png;base64,") == len(REPORT_CHARTS) and "chart://" not in html_report


def test_the_key_figures_are_the_verdicts_own_numbers(app):
    st = read_station_csv(EXAMPLE)
    res = assess_station(st, 500, ambient_for(st.index))
    rows = dict(key_figures(res))
    v = res.verdict
    assert rows["verdict"] == v.headline() and rows["verdict"].startswith(v.level)
    assert f"{v.on_paper['peak load (% of nameplate)']:.1f} % of nameplate" in rows["peak load vs rating"]
    assert f"hot-spot above 120 °C: {v.really['hours hot-spot above 120 °C']:,.0f} h" in \
        rows[f"hours above the IEC normal cyclic limits ({IEC_TABLE_4})"]
    rate = res.thermal.equivalent_ageing_factor                       # a light station: far below 0.1
    assert 0 < rate < 0.1 and rows["relative ageing rate"].startswith(f"{rate:.2g} on average")   # not "0.00"
    assert f"{v.really['days of life used per year']:.3f} days of life used per year" in rows["relative ageing rate"]


def test_the_pdf_report_is_written_with_its_title(app, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    out = tmp_path / "report.pdf"
    assert main([str(EXAMPLE), "--kva", "500", "--out", str(out)]) == 0
    r = pypdf.PdfReader(str(out))
    assert len(r.pages) >= 4 and r.metadata.title == "Hotspot LV station report — ns-102.csv"


def test_a_custom_unit_far_too_small_shows_the_withdrawn_figures(app, tmp_path):
    out = tmp_path / "small.html"
    assert main([str(EXAMPLE), "--kva", "125", "--p0", "160", "--pk", "1500", "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "custom unit" in page and "withdrawn: loading above 2 × rated" in page
    assert page.count("src='data:image/png;base64,") == len(REPORT_CHARTS) - 1      # no temperatures to sort


@pytest.mark.parametrize("argv, words", [
    (["--kva", "300"], "no catalogue data for 300 kVA"),
    (["--kva", "500", "--p0", "459"], "both --p0 and --pk"),
    (["--kva", "500", "--out", "r.docx"], "must end in .pdf or .html"),
])
def test_wrong_inputs_say_why_and_return_1(app, tmp_path, capsys, argv, words):
    argv = [a if a != "r.docx" else str(tmp_path / "r.docx") for a in argv]
    assert main([str(EXAMPLE), *argv]) == 1
    assert words in capsys.readouterr().out


def test_arguments_are_checked_before_anything_runs():
    with pytest.raises(SystemExit) as e:
        parser().parse_args([str(EXAMPLE)])                    # --kva is required
    assert e.value.code == 2


def test_the_command_runs_in_its_own_process_and_says_where_the_report_is(tmp_path):
    """The real entry point (python -m hotspotlv report …): no window, exit code 0, a summary on stdout."""
    out = tmp_path / "r.html"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p), "QT_QPA_PLATFORM": "offscreen",
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "hotspotlv", "report", str(EXAMPLE), "--kva", "500", "--out", str(out)],
                       capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    assert f"report: {out.resolve()}" in r.stdout and INDICATIVE in r.stdout and out.is_file()
    bad = subprocess.run([sys.executable, "-m", "hotspotlv", "report", str(tmp_path / "missing.csv"), "--kva", "500"],
                         capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert bad.returncode == 1 and "is not a file" in bad.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="the windows platform plugin gives the real fonts")
def test_the_commands_pdf_carries_real_text_not_boxes(tmp_path):
    """Catches: the report drawn on Qt's offscreen platform, which on Windows has no fonts and prints every letter
    as a box — the suite runs offscreen, so only a separate process with the normal platform sees it."""
    fitz = pytest.importorskip("fitz")
    out = tmp_path / "r.pdf"
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env.update(PYTHONPATH=os.pathsep.join(p for p in sys.path if p), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "hotspotlv", "report", str(EXAMPLE), "--kva", "500", "--out", str(out)],
                       capture_output=True, text=True, timeout=240, env=env, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    doc = fitz.open(str(out))
    text = " ".join(page.get_text() for page in doc).replace("\n", " ")
    for words in ("Hotspot LV — station report", INDICATIVE, "Key figures", "What was read from the file",
                  "Temperature duration curve", "relative ageing rate"):
        assert words in text, words
    assert all(page.get_text().strip().endswith(f"page {i + 1} of {len(doc)}") for i, page in enumerate(doc))


def test_the_key_figures_and_the_temperature_curve_count_quarter_hours_as_quarter_hours(app):
    """Catches: a count of steps shown as hours — 4× too many on every quarter-hour export (all the reports' other
    tests use an hourly file, whose limits are never reached)."""
    import numpy as np
    import pandas as pd
    from hotspotlv.app.widgets.charts import TemperatureDuration
    from hotspotlv.station import StationSeries
    idx = pd.date_range("2025-01-06", periods=7 * 96, freq="15min", tz="Europe/Stockholm").tz_convert("UTC")
    hour = idx.tz_convert("Europe/Stockholm").hour
    p = pd.Series(np.where((hour >= 14) & (hour < 22), 760.0, 300.0), index=idx)        # ≈ 1.7 × 500 kVA, 8 h a day
    st = StationSeries(p, p * 0.2, None, 15.0, "meters", "q.csv")
    res = assess_station(st, 500, pd.Series(20.0, index=idx))
    k = res.year.hourly["loading_pct"] / 100
    hours_above = float((k > 1.5).sum() * 0.25)
    assert res.verdict.really["max load (× rated)"] < 2 and res.verdict.really["max top-oil (°C)"] > 105
    assert hours_above == pytest.approx(56.0, abs=0.5)                                  # 8 h × 7 days, not 224
    row = dict(key_figures(res))[f"hours above the IEC normal cyclic limits ({IEC_TABLE_4})"]
    assert f"load above 1.5 × rated: {hours_above:,.0f} h" in row
    top_oil_h = float((res.thermal.hourly["theta_o"] > 105).sum() * 0.25)
    assert f"top-oil above 105 °C: {top_oil_h:,.0f} h" in row
    chart = TemperatureDuration()
    chart.set_thermal(res.thermal.hourly, 0.25)
    x, _ = chart.hot.getData()
    assert x[-1] == pytest.approx(len(idx) * 0.25)                                       # 168 h, not 672
    assert f"{res.verdict.really['hours hot-spot above 120 °C']:,.1f} h" in chart.caption.text()
