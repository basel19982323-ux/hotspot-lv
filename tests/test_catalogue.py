"""The transformer catalogue (v0.2: 50–1000 kVA) and custom units — and proof that the v0.1 sizes, and so every
study result, did not move."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hotspotlv.catalogue import (CUSTOM_UK_PERCENT, SOURCES, TRANSFORMERS, custom_transformer, transformer)
from hotspotlv.station import assess_station, s_from_meters
from hotspotlv.thermal import ThermalParams, simulate

from test_thermal import ANNEX_C_K, ANNEX_C_THETA_A

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

# Table I.1 of Regulation (EU) No 548/2014 as amended by (EU) 2019/1783, Tier 2: Ak (W) and A0 − 10 % (W),
# transcribed from the legislation.gov.uk copy of Annex I on 31 Oct 2026
TABLE_I1_TIER2 = {50: (750, 81), 100: (1250, 130), 160: (1750, 189), 250: (2350, 270), 315: (2800, 324),
                  400: (3250, 387), 500: (3900, 459), 630: (4600, 540), 800: (6000, 585), 1000: (7600, 693)}
# Ellevio, technical specification oil-immersed distribution transformers rev 3.0 (2022), §11, uk at 75 °C
ELLEVIO_UK = {50: 3.5, 100: 3.5, 200: 3.5, 315: 4.0, 500: 4.0, 800: 4.5, 1000: 6.0}


def test_the_v01_sizes_are_unchanged_to_the_byte():
    """Catches any edit of the three rows the study ran on — their text in the source, not only their values."""
    text = (ROOT / "src" / "hotspotlv" / "catalogue.py").read_text(encoding="utf-8")
    for line in ("    315: dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0),\n",
                 "    500: dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0),\n",
                 "    800: dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0),\n"):
        assert text.count(line) == 1, line
    assert TRANSFORMERS[315] == dict(p_load_w=2800, p_noload_w=324, uk_percent=4.0)
    assert TRANSFORMERS[500] == dict(p_load_w=3900, p_noload_w=459, uk_percent=6.0)
    assert TRANSFORMERS[800] == dict(p_load_w=6000, p_noload_w=585, uk_percent=6.0)


def test_the_catalogue_has_the_sizes_asked_for():
    assert sorted(TRANSFORMERS) == [50, 100, 160, 200, 250, 315, 400, 500, 630, 800, 1000]


@pytest.mark.parametrize("kva", sorted(TABLE_I1_TIER2))
def test_losses_are_table_i1_tier2(kva):
    pk, p0 = TABLE_I1_TIER2[kva]
    assert (TRANSFORMERS[kva]["p_load_w"], TRANSFORMERS[kva]["p_noload_w"]) == (pk, p0)


def test_200_kva_is_interpolated_as_the_regulation_says():
    """'Maximum losses for kVA ratings that fall in between … shall be obtained by linear interpolation.'"""
    f = (200 - 160) / (250 - 160)
    assert TRANSFORMERS[200]["p_load_w"] == pytest.approx(1750 + f * (2350 - 1750), abs=0.05)
    assert TRANSFORMERS[200]["p_noload_w"] == pytest.approx(189 + f * (270 - 189), abs=1e-9)


def test_uk_follows_ellevio_up_to_315_kva_and_the_v01_rule_from_500():
    for kva in (50, 100, 200, 1000):                                      # the v0.2 sizes Ellevio lists
        assert TRANSFORMERS[kva]["uk_percent"] == ELLEVIO_UK[kva]
    assert TRANSFORMERS[160]["uk_percent"] == 3.5                          # inside Ellevio's 50–200 kVA band
    assert TRANSFORMERS[250]["uk_percent"] == 4.0                          # the higher neighbour (315 kVA)
    assert TRANSFORMERS[400]["uk_percent"] == 5.0                          # between 4 % (315) and 6 % (500)
    assert TRANSFORMERS[630]["uk_percent"] == 6.0                          # the v0.1 rule from 500 kVA


def test_a_bigger_unit_never_has_a_larger_transformer_voltage_drop():
    """Catches (v0.2 review): 4 % at 400 and 630 kVA next to 6 % at 500 and 800 kVA made the bigger unit of a
    Fixes comparison look worse in voltage. Checked on the drop formula and on the power flow itself."""
    import math
    from hotspotlv.feeder import build_feeder, run_snapshot
    sizes = sorted(TRANSFORMERS)
    drop = []
    for kva in sizes:
        t = TRANSFORMERS[kva]
        vkr = 100 * t["p_load_w"] / (kva * 1000)
        drop.append((vkr * 0.95 + math.sqrt(t["uk_percent"] ** 2 - vkr ** 2) * 0.312) / kva)   # % per kVA at pf 0.95
    assert all(a > b for a, b in zip(drop, drop[1:])), list(zip(sizes, drop))
    vmin = []
    for kva in [k for k in sizes if k >= 250]:
        net = build_feeder(n_houses=60, n_feeders=6, trafo_kva=kva, house_p_kw=4.0)
        vmin.append(run_snapshot(net)["vmin_pu"])
    assert all(a < b for a, b in zip(vmin, vmin[1:])), vmin


def test_every_size_has_its_sources_written_down():
    """Catches a size added without saying where its numbers come from (the Assumptions tab prints these)."""
    assert set(SOURCES) == set(TRANSFORMERS)
    for kva, (loss_src, uk_src) in SOURCES.items():
        assert "Table I.1" in loss_src and uk_src, kva
    assert "interpolated" in SOURCES[200][0]
    for kva in (160, 250, 400, 630):
        assert "assumed" in SOURCES[kva][1], kva


def test_every_size_passes_the_custom_plausibility_checks():
    """The custom-unit ranges must accept every catalogue row — else they would refuse real units."""
    for kva, tr in TRANSFORMERS.items():
        c = custom_transformer(kva, tr["p_noload_w"], tr["p_load_w"], tr["uk_percent"])
        assert c["p_load_w"] == tr["p_load_w"] and c["p_noload_w"] == tr["p_noload_w"]


def test_an_unknown_size_says_how_to_go_on():
    with pytest.raises(ValueError, match="no-load and load losses"):
        transformer(300)


# --- custom units -------------------------------------------------------------------------------------------
def test_a_custom_unit_keeps_the_entered_losses_and_says_when_uk_is_assumed():
    c = custom_transformer(125, 160, 1500)
    assert c == dict(p_load_w=1500.0, p_noload_w=160.0, uk_percent=CUSTOM_UK_PERCENT, uk_given=False)
    assert custom_transformer("125", "160", "1500", "4.5")["uk_given"] is True


@pytest.mark.parametrize("args, words", [
    ((500, 0.459, 3.9), "watts"),                  # kW typed where watts are asked
    ((500, 459_000, 3_900_000), "watts"),
    ((500, 3900, 459), "watts|swapped"),           # no-load and load loss swapped: caught by a range or the order
    ((500, 900, 800), "swapped"),
    ((10, 50, 400), "outside"),                    # below the distribution range
    ((5000, 3000, 30000), "outside"),
    ((3150, 2000, 20000), "outside"),              # above 2 500 kVA: not a distribution transformer's constants
    ((500, 0.459, 3900), "no-load loss"),          # the no-load loss alone typed in kW
    ((500, 459, 3900, 25), "uk"),
    ((500, "x", 3900), "numbers"),
])
def test_custom_values_that_cannot_be_right_are_refused(args, words):
    with pytest.raises(ValueError, match=words):
        custom_transformer(*args)


def test_custom_losses_reach_the_station_physics_and_the_thermal_constants():
    idx = pd.date_range("2025-01-01", periods=24, freq="h", tz="Europe/Stockholm")
    p, q = pd.Series(300.0, index=idx), pd.Series(90.0, index=idx)
    same = custom_transformer(500, 459, 3900, 6.0)                          # = the 500 kVA catalogue row
    assert np.allclose(s_from_meters(p, q, 500, losses=same), s_from_meters(p, q, 500))
    lossy = custom_transformer(500, 900, 7000, 6.0)
    assert (s_from_meters(p, q, 500, losses=lossy) > s_from_meters(p, q, 500)).all()
    assert ThermalParams.for_transformer(500, losses=same) == ThermalParams.for_transformer(500)
    assert ThermalParams.for_transformer(125, losses=custom_transformer(125, 160, 1500)).R == pytest.approx(1500 / 160)


def test_a_custom_station_run_is_labelled_as_such():
    idx = pd.date_range("2025-01-06", periods=7 * 24, freq="h", tz="Europe/Stockholm")
    from hotspotlv.station import StationSeries
    st = StationSeries(p_kw=pd.Series(90.0, index=idx), q_kvar=pd.Series(25.0, index=idx), s_kva_measured=None, step_min=60,
                       measured_at="meters", source="t.csv")
    amb = pd.Series(-5.0, index=idx)
    res = assess_station(st, 125, amb, losses=custom_transformer(125, 160, 1500))
    assert res.losses["p_load_w"] == 1500 and res.rated_kva == 125
    note = next(n for n in res.verdict.notes if "entered by the user" in n)
    assert "1500 W" in note and "assumed" in note
    with pytest.raises(ValueError, match="no catalogue data for 125 kVA"):
        assess_station(st, 125, amb)


# --- regression: the thermal model and the study did not move ---------------------------------------------
# The IEC 60076-7 Annex C load cycle (41 steps of 3 min) run on the catalogue units with their own distribution
# constants. Reference values from the v0.1 code (commit 21ccb9c, catalogue 315/500/800 only), 31 Oct 2026.
V01_ANNEX_C_ON_CATALOGUE = {
    # kVA: (R, Δθor K, last hot-spot °C, max hot-spot °C, max top-oil °C, loss of life days)
    315: (8.641975308641975, 60.65, 109.08942549781717, 142.42827568438747, 95.12806668102448, 3.8457678155225588),
    500: (8.49673202614379, 62.5, 111.01245998583734, 144.29012297247326, 97.13494296041405, 4.763831059003206),
    800: (10.256410256410257, 65.0, 113.63605546795561, 146.78133807929112, 99.93173432375744, 6.343568987951623),
}


@pytest.mark.parametrize("kva", sorted(V01_ANNEX_C_ON_CATALOGUE))
def test_the_annex_c_cycle_on_the_v01_units_gives_the_v01_hot_spot(kva):
    idx = pd.date_range("2000-01-01 14:00", periods=len(ANNEX_C_K), freq="3min")
    p = ThermalParams.for_transformer(kva)
    r = simulate(pd.Series(ANNEX_C_K, index=idx), pd.Series(ANNEX_C_THETA_A, index=idx), p, step_min=3.0)
    got = (p.R, p.d_theta_or, r.hourly["theta_h"].iloc[-1], r.hourly["theta_h_max"].max(), r.hourly["theta_o"].max(),
           r.loss_of_life_days)
    assert got == pytest.approx(V01_ANNEX_C_ON_CATALOGUE[kva], rel=1e-12)


def test_one_study_case_on_the_315_kva_unit_reproduces_its_committed_row():
    """Re-runs the village road (315 kVA), coldest-year row 2009 / 77 houses — the first Watch in that year — and
    compares with study/results/runs_fine.csv, written by the v0.1 code."""
    sys.path.insert(0, str(ROOT / "study"))
    import run_study
    row = pd.read_csv(ROOT / "study" / "results" / "runs_fine.csv").query("street == 'rural' and year == 2009 and houses == 77")
    assert len(row) == 1
    row = row.iloc[0]
    now = run_study.run_one("rural", 2009, 77)
    assert now["kVA"] == 315 and now["verdict"] == row["verdict"] == "Watch"
    for c in ("max_hot_spot_c", "max_top_oil_c", "life_days_per_year", "peak_loading_pct", "max_load_pu",
              "hours_above_80", "hours_above_100", "voltage_bad_hours", "voltage_plan_hours", "max_cable_pct",
              "energy_mwh"):
        assert now[c] == pytest.approx(row[c], rel=1e-9), c
    for c in ("rules_watch", "rules_at_risk"):
        assert (now[c] or "") == ("" if pd.isna(row[c]) else row[c]), c


def test_the_assumptions_page_lists_every_size_with_its_sources():
    from hotspotlv.app.main_window import _assumptions_markdown
    from hotspotlv.catalogue import catalogue_markdown
    page = _assumptions_markdown()
    table = catalogue_markdown()
    assert table in page and "Custom unit" in page
    rows = [r for r in table.splitlines() if r.startswith("| ") and r[2].isdigit()]
    assert [int(r.split("|")[1]) for r in rows] == sorted(TRANSFORMERS)
    assert "| 200 | 2016.7 | 225 | 3.5 |" in table and "interpolated" in table
    assert "| 400 | 3250 | 387 | 5 |" in table


def test_a_fleet_index_row_can_be_a_custom_unit_and_a_wrong_one_is_an_error_row(tmp_path):
    from hotspotlv.fleet import read_fleet_index
    (tmp_path / "idx.csv").write_text(
        "station_id;rated_kva;file;p_noload_w;p_load_w;uk_percent\n"
        "A;500;a.csv;;;\n"
        "B;125;b.csv;160;1 500;4,5\n"
        "C;125;c.csv;160;;\n"
        "D;125;d.csv;0,16;1,5;\n", encoding="utf-8")
    a, b, c, d = read_fleet_index(tmp_path / "idx.csv")
    assert a.losses is None and a.error is None
    assert b.losses == dict(p_load_w=1500.0, p_noload_w=160.0, uk_percent=4.5, uk_given=True) and b.error is None
    assert "both p_noload_w and p_load_w" in c.error
    assert "watts" in d.error                                               # kW typed where watts are asked


def test_the_station_panel_offers_every_size_and_a_custom_unit(app):
    """Catches: a custom unit whose fields are not read, or that slips past the plausibility checks."""
    from hotspotlv.app.widgets.station_panel import StationPanel
    panel = StationPanel()
    assert panel.rating() == (500.0, None) and not panel.custom_box.isVisibleTo(panel)
    panel.kva.setCurrentIndex(panel.kva.findData("custom"))
    assert panel.custom_box.isVisibleTo(panel)
    panel.custom_kva.setValue(125); panel.custom_p0.setValue(160); panel.custom_pk.setValue(1500)
    kva, losses = panel.rating()
    assert kva == 125 and losses == dict(p_load_w=1500.0, p_noload_w=160.0, uk_percent=CUSTOM_UK_PERCENT, uk_given=False)
    panel.custom_uk.setValue(4.5)
    assert panel.rating()[1]["uk_percent"] == 4.5
    before = panel._inputs()
    panel.custom_pk.setValue(1600)
    assert panel._inputs() != before                         # a changed loss makes the card on screen stale
    panel.custom_pk.setValue(100)                            # below the no-load loss
    with pytest.raises(ValueError, match="swapped|watts"):
        panel.rating()


def test_custom_losses_reach_a_fleet_run(tmp_path):
    """Catches: screen_fleet dropping a custom unit's losses — the catalogue values would be used, silently."""
    from hotspotlv.fleet import read_fleet_index, screen_fleet
    from hotspotlv.paths import package_data
    example = package_data() / "examples" / "ns-101.csv"
    lines = ["station_id;rated_kva;file;p_noload_w;p_load_w", f"A;500;{example};;", f"B;500;{example};900;9000"]
    (tmp_path / "idx.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    table, results = screen_fleet(read_fleet_index(tmp_path / "idx.csv"))
    assert results["A"].losses is None and results["B"].losses["p_load_w"] == 9000
    load = results["A"].station.p_kw.to_numpy() > 0                       # (in export hours losses lower |S|)
    assert (results["B"].year.hourly["s_kva"].to_numpy() > results["A"].year.hourly["s_kva"].to_numpy())[load].all()
    assert results["B"].thermal.params.R == pytest.approx(10.0)


def test_the_station_panel_gives_the_worker_its_custom_unit(app, monkeypatch):
    """Catches: run() forgetting the custom losses on the way to the worker thread."""
    from hotspotlv.app.widgets import station_panel as spm
    from hotspotlv.paths import package_data
    seen = {}

    class Recorder:
        def __init__(self, fn, *args, **kw):
            seen["args"] = args
            self.finished_ok = self.failed = type("S", (), {"connect": lambda *_: None})()

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(spm, "Worker", Recorder)
    panel = spm.StationPanel()
    panel.path.setText(str(package_data() / "examples" / "ns-102.csv"))
    panel.kva.setCurrentIndex(panel.kva.findData("custom"))
    panel.custom_kva.setValue(125); panel.custom_p0.setValue(160); panel.custom_pk.setValue(1500)
    panel.run()
    args = seen["args"]
    assert args[1] == 125 and args[11]["p_load_w"] == 1500.0 and args[11]["p_noload_w"] == 160.0
