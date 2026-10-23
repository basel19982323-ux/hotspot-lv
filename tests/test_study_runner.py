"""The study's own scripts (study/): street types, the flag mapping, the meter rule, refinement, results and charts."""

import calendar
import dataclasses
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

STUDY = Path(__file__).resolve().parents[1] / "study"
RESULTS = STUDY / "results"
sys.path.insert(0, str(STUDY))
import run_study  # noqa: E402

from hotspotlv.thermal import ThermalParams, simulate  # noqa: E402
from hotspotlv.timeseries import YearResult  # noqa: E402
from hotspotlv.verdict import judge  # noqa: E402


@pytest.mark.parametrize("street, houses, cables, kva, seg", [
    ("rural", 10, 2, 315, 0.060), ("rural", 100, 13, 315, 0.060),
    ("suburb", 90, 6, 500, 0.030), ("suburb", 160, 11, 500, 0.030),
    ("dense", 260, 11, 800, 0.015),
])
def test_a_street_grows_by_cables_of_its_own_type(street, houses, cables, kva, seg):
    case = run_study.case_for(street, houses)
    assert case.n_feeders == cables == math.ceil(houses / run_study.STREETS[street]["per_cable"])
    assert case.trafo_kva == kva and case.main_segment_km == seg and case.scenario.n_houses == houses
    assert case.scenario.ev_share == 0.6 and case.scenario.pv_share == 0.4 and case.scenario.seed == 0


def test_run_case_builds_the_network_with_the_case_spacing(monkeypatch):
    """Catches: Case.main_segment_km set but never passed on — every street would run at 30 m."""
    import hotspotlv.study as st
    seen = {}

    class Stop(Exception):
        pass

    def spy(**kw):
        seen.update(kw)
        raise Stop
    monkeypatch.setattr(st, "build_feeder", spy)
    with pytest.raises(Stop):
        st.run_case(run_study.case_for("rural", 10), run_study.weather(2023))
    assert seen == dict(n_houses=10, n_feeders=2, trafo_kva=315, main_segment_km=0.060)


# ---------------------------------------------------------------- the flag mapping against verdict.py's rule names
def _year(lp, vmin=0.97, vmax=1.01, cable=50.0, n=8760):
    idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    lp = np.full(n, float(lp)) if np.isscalar(lp) else np.asarray(lp, float)
    h = pd.DataFrame({"s_kva": lp * 5, "loading_pct": lp, "p_kw": lp * 4.75, "vmin_pu": vmin, "vmax_pu": vmax,
                      "losses_kw": 1.0, "line_max_pct": cable}, index=idx)
    return YearResult(h, 500.0)


def _verdict(lp, amb=5.0, **kw):
    yr = _year(lp, **kw)
    ta = pd.Series(float(amb), index=yr.hourly.index)
    return judge(yr, simulate(yr.hourly["loading_pct"] / 100, ta, ThermalParams.for_transformer(500, enclosure="outdoor")))


def _spike(base, i, n, val):
    a = np.arange(8760)
    return np.where((a >= i) & (a < i + n), val, base).astype(float)


EXPECTED = {
    "ageing faster than design": "thermal", "ageing above half the design rate": "thermal",
    "hot-spot above long-time emergency limit": "thermal", "hot-spot above normal cyclic limit": "thermal",
    "top-oil above long-time emergency limit": "thermal", "top-oil above normal cyclic limit": "thermal",
    "load above long-time emergency current limit": "thermal", "load above normal cyclic current limit": "thermal",
    "house voltage outside ±10 %": "voltage", "house voltage outside the 0.92–1.05 pu planning band": "voltage",
    "a cable above its current rating": "cable",
}


def test_every_verdict_rule_maps_to_the_category_the_study_expects():
    """Catches: a rule renamed in verdict.py — the study's flag would silently turn False (45 cells for hot-spot)."""
    from hotspotlv.verdict import OUT_OF_RANGE_RULE
    vs = [_verdict(130.0, 20), _verdict(_spike(20, 4000, 3, 155), 25), _verdict(_spike(20, 4000, 6, 150), 25),
          _verdict(130.0, 40), _verdict(130.0, 30), _verdict(100.0, 17), _verdict(_spike(20, 100, 1, 185), -20),
          _verdict(_spike(60, 4000, 10, 220)), _verdict(30.0, vmin=0.89),
          _verdict(30.0, vmin=_spike(0.97, 0, 10, 0.915)), _verdict(30.0, cable=110.0)]
    emitted = {r.rule for v in vs for r in v.reasons}
    want = set(EXPECTED) | {OUT_OF_RANGE_RULE}
    assert emitted == want, (want - emitted, emitted - want)
    cats = {"thermal": run_study.THERMAL_RULES, "voltage": run_study.VOLTAGE_RULES, "cable": run_study.CABLE_RULES}
    for rule in emitted:
        hits = [c for c, keys in cats.items() if run_study._has([rule], keys)]
        assert hits == [EXPECTED.get(rule, "thermal")], (rule, hits)
    for keys in cats.values():
        for k in keys:
            assert any(r.startswith(k) for r in emitted), k                    # no dead prefix


# ---------------------------------------------------------------- the 80 % rule on summed meters
def test_the_meter_sum_adds_each_houses_import_against_the_kva_rating(monkeypatch):
    """The rule sums what each meter draws: one house exporting does not hide another house's load."""
    idx = pd.date_range("2023-01-01", periods=3, freq="h", tz="UTC")
    cons = pd.DataFrame({"a": [100.0, 300.0, 10.0], "b": [50.0, 0.0, 0.0]}, index=idx)
    gen = pd.DataFrame({"a": [0.0, 0.0, 0.0], "b": [80.0, 0.0, 0.0]}, index=idx)
    import hotspotlv.neighbourhood as nbm
    monkeypatch.setattr(nbm, "build_neighbourhood", lambda sc, wx: SimpleNamespace(cons_kw=cons, gen_kw=gen))
    m = run_study.meter_sum("rural", 2023, 2)                                # 315 kVA
    assert m["meter_peak_pct"] == pytest.approx(300 / 315 * 100)
    assert m["meter_hours_above_80"] == 1 and m["meter_hours_above_100"] == 0
    assert run_study.meter_sum.__doc__ and "2018:552" in run_study.meter_sum.__doc__


def test_the_meter_sum_uses_the_same_houses_as_the_full_run():
    from hotspotlv.neighbourhood import build_neighbourhood
    nb = build_neighbourhood(run_study.scenario(12), run_study.weather(2023))
    m = run_study.meter_sum("suburb", 2023, 12)
    assert m["meter_peak_pct"] == pytest.approx(float((nb.cons_kw - nb.gen_kw).clip(lower=0).sum(axis=1).max()) / 5)


# ---------------------------------------------------------------- first limits, refinement and ratios
def _runs(rows):
    base = {"street": "suburb", "year": 2010, "grid": True, "watch_thermal": False, "at_risk_thermal": False,
            "at_risk_voltage": False, "watch_cable": False}
    return pd.DataFrame([base | r for r in rows])


def test_the_first_limit_is_the_smallest_house_count_that_meets_it_in_any_row_order():
    """Catches: taking the first row instead of the smallest count (runs.csv is written in completion order)."""
    import figures
    runs = _runs([{"houses": 70, "watch_thermal": True}, {"houses": 50}, {"houses": 60},
                  {"houses": 65, "grid": False, "watch_thermal": True},
                  {"houses": 66, "grid": False, "watch_thermal": True, "at_risk_thermal": True},
                  {"houses": 60, "year": 2020}, {"houses": 70, "year": 2020, "at_risk_voltage": True},
                  {"houses": 55, "watch_cable": True, "grid": False}])            # a refined cable row is ignored
    runs = runs.drop_duplicates(["street", "year", "houses"]).sample(frac=1, random_state=1)
    meters = pd.DataFrame({"street": "suburb", "year": [2010] * 4 + [2020] * 2, "houses": [50, 60, 55, 70, 60, 70],
                           "meter_peak_pct": [70.0, 85.0, 81.0, 101.0, 60.0, 70.0], "grid": True})
    first = figures.first_houses(runs, meters).set_index(["year", "criterion"])["houses"]
    assert first[(2010, figures.RULE80)] == 55 and first[(2010, figures.METER100)] == 70
    assert first[(2010, figures.WATCH)] == 65 and first[(2010, figures.RISK)] == 66
    assert np.isnan(first[(2010, figures.CABLE)])                             # grid only for the cable
    assert first[(2020, figures.VOLT)] == 70 and np.isnan(first[(2020, figures.RULE80)])   # not reached: no number


def test_the_13_year_median_counts_a_year_that_did_not_reach_the_limit_as_beyond():
    import figures
    reached = pd.Series([75, 85, 85, 85, 85, 90, 95, 95, 100] + [np.nan] * 4, dtype=float)
    assert figures.median_13(reached) == 95                                  # not 85, the median of the 9 reached
    assert figures.median_13(pd.Series([60.0] + [np.nan] * 12)) == np.inf


def test_refinement_fills_the_step_below_each_first_grid_count():
    import refine
    grid = pd.DataFrame({"street": "rural", "year": 2010, "houses": [10, 15, 20, 25],
                         "flag": [False, False, True, True]})
    gaps = refine.gaps_below_first(grid, grid["flag"])
    assert list(gaps[("rural", 2010)]) == [16, 17, 18, 19]
    assert list(refine.gaps_below_first(grid, grid["houses"] > 0)[("rural", 2010)]) == []    # first = smallest
    assert refine.gaps_below_first(grid, grid["houses"] > 99) == {}                          # never met


def test_limits_by_variation_and_the_ratio_to_the_80_rule():
    import figures
    lim = pd.DataFrame({"street": "rural", "year": 2010, "variation": ["base"] * 4 + ["y 2.0"] * 4,
                        "houses": [10, 20, 30, 40] * 2,
                        "watch_thermal": [False, True, False, False] + [False] * 4,
                        "at_risk_thermal": [False, False, True, True] + [False] * 4})
    first = pd.DataFrame({"street": ["rural"] * 2, "year": [2010] * 2, "criterion": [figures.RULE80, figures.METER100],
                          "houses": [10.0, 20.0], "range_max": 40.0})
    out = figures.limits_by_variation(lim, first).set_index("variation")
    assert out.loc["base", ["watch", "at_risk", "rule80", "range_max"]].tolist() == [20, 30, 10, 40]
    assert out.loc["base", "at_risk_vs_rule80"] == 3.0 and out.loc["base", "watch_vs_rule80"] == 2.0
    assert np.isnan(out.loc["y 2.0", "watch"]) and np.isnan(out.loc["y 2.0", "at_risk_vs_rule80"])


def test_reference_station_is_the_first_median_peak_above_nameplate():
    import sensitivity
    runs = pd.DataFrame({"street": "suburb", "houses": [50] * 3 + [60] * 3 + [70] * 3,
                         "peak_loading_pct": [99, 101, 98, 95, 101, 102, 120, 130, 99]})   # medians 99, 101, 120
    assert sensitivity.reference_houses(runs) == {"suburb": 60}


# ---------------------------------------------------------------- resume
def test_resume_runs_only_what_is_missing_and_a_failed_run_fails_the_script(tmp_path, monkeypatch):
    calls, fail = [], {"on": True}

    def fake(street, year, houses):
        calls.append((street, year, houses))
        if (year, houses) == (2010, 15) and fail["on"]:
            raise RuntimeError("one run fails")
        return {f: 0 for f in run_study.FIELDS} | {"street": street, "year": year, "houses": houses}
    monkeypatch.setattr(run_study, "run_one", fake)
    monkeypatch.setattr(run_study, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setitem(run_study.STREETS, "rural", dict(run_study.STREETS["rural"], houses=[10, 15]))
    out = tmp_path / "runs.csv"
    monkeypatch.setattr(sys, "argv", ["run_study.py", "--only", "rural", "--years", "2010,2020", "--workers", "2",
                                      "--out", str(out)])
    assert run_study.main() == 1                                            # a hole must not pass silently
    assert len(pd.read_csv(out)) == 3
    calls.clear()
    fail["on"] = False
    assert run_study.main() == 0
    assert calls == [("rural", 2010, 15)]
    df = pd.read_csv(out)
    assert list(df.columns) == run_study.FIELDS and len(df) == 4
    assert not df.duplicated(["street", "year", "houses"]).any()
    out.write_text(out.read_text(encoding="utf-8") + "rural,Rural", encoding="utf-8")       # a cut-off row
    with pytest.raises(SystemExit, match="cut-off"):
        run_study.main()
    out.write_text("street,houses\n", encoding="utf-8")                                     # other columns
    with pytest.raises(SystemExit, match="other columns"):
        run_study.main()


# ---------------------------------------------------------------- the committed results agree with each other
def test_committed_runs_cover_every_planned_case_once_and_flags_match_rules():
    runs = pd.read_csv(RESULTS / "runs.csv")
    planned = {(s, y, n) for s in run_study.STREETS for y in run_study.YEARS for n in run_study.STREETS[s]["houses"]}
    assert list(runs.columns) == run_study.FIELDS
    assert set(zip(runs.street, runs.year, runs.houses)) == planned and len(runs) == len(planned)

    def split(s):
        return [] if pd.isna(s) else s.split(" | ")
    for frame in (runs, pd.read_csv(RESULTS / "runs_fine.csv")):
        for flag, col, keys in [("at_risk_thermal", "rules_at_risk", run_study.THERMAL_RULES),
                                ("watch_thermal", "rules_watch", run_study.THERMAL_RULES),
                                ("at_risk_voltage", "rules_at_risk", run_study.VOLTAGE_RULES),
                                ("watch_cable", "rules_watch", run_study.CABLE_RULES)]:
            assert (frame[col].map(lambda s: run_study._has(split(s), keys)) == frame[flag]).all(), flag
        assert (frame.life_days_per_year.isna() == (frame.max_load_pu > 2.0)).all()
        assert frame.max_top_oil_c.notna().sum() == frame.life_days_per_year.notna().sum()
    meters = pd.read_csv(RESULTS / "meter_sums.csv")
    assert set(zip(meters.street, meters.year, meters.houses)) == planned and len(meters) == len(planned)


def test_sensitivity_base_reproduces_runs_csv():
    runs = pd.read_csv(RESULTS / "runs.csv")
    for f in ("sensitivity_limits.csv", "sensitivity.csv"):
        s = pd.read_csv(RESULTS / f)
        base = s[s.variation.str.startswith("base")].reset_index(drop=True)
        m = base.merge(runs, on=["street", "year", "houses"], suffixes=("_s", "_r"), validate="one_to_one")
        assert len(m) == len(base) > 0
        for c in ("peak_loading_pct", "life_days_per_year", "max_hot_spot_c"):
            pd.testing.assert_series_equal(m[c + "_s"], m[c + "_r"], check_names=False, rtol=1e-9)
        if "at_risk_thermal_s" in m:
            assert (m.at_risk_thermal_s == m.at_risk_thermal_r).all() and (m.watch_thermal_s == m.watch_thermal_r).all()
        if "verdict_s" in m:
            assert (m.verdict_s == m.verdict_r).all()


# ---------------------------------------------------------------- charts end to end
def test_figures_run_end_to_end_on_tiny_synthetic_results(tmp_path, monkeypatch):
    import figures
    import sensitivity
    monkeypatch.setattr(figures, "FIG", tmp_path / "fig")
    monkeypatch.setattr(run_study, "RESULTS", tmp_path)
    for s in list(run_study.STREETS):
        monkeypatch.setitem(run_study.STREETS, s, dict(run_study.STREETS[s], houses=run_study.STREETS[s]["houses"][:3]))
    idx = pd.date_range("2023-01-01", periods=48, freq="h", tz="UTC")
    monkeypatch.setattr(run_study, "weather",
                        lambda y: pd.DataFrame({"temp_c": -12.0 + (y % 7), "pv_kw_per_kwp": 0.0}, index=idx))
    rows, mrows = [], []
    for s in run_study.STREETS:
        for y in run_study.YEARS:
            for i, n in enumerate(run_study.STREETS[s]["houses"]):
                rows.append({f: 0 for f in run_study.FIELDS} | dict(
                    street=s, year=y, houses=n, peak_loading_pct=70 + 20 * i, life_days_per_year=[10, 200, np.nan][i],
                    watch_thermal=i == 1, at_risk_thermal=i == 2, at_risk_voltage=False, watch_cable=False))
                mrows.append(dict(street=s, year=y, houses=n, meter_peak_pct=75 + 10 * i, meter_hours_above_80=5 * i,
                                  meter_hours_above_100=0))
    pd.DataFrame(rows).to_csv(tmp_path / "runs.csv", index=False)
    pd.DataFrame(mrows).to_csv(tmp_path / "meter_sums.csv", index=False)
    names = [n for n, _, _ in sensitivity.variations(ThermalParams())]
    ref = {s: run_study.STREETS[s]["houses"][1] for s in run_study.STREETS}
    pd.DataFrame([dict(street=s, label="", houses=ref[s], year=y, variation=v, peak_loading_pct=110.0,
                       life_days_per_year=100.0 + k, max_hot_spot_c=110.0, verdict="Watch", why="")
                  for s in run_study.STREETS for y in sensitivity.YEARS
                  for k, v in enumerate(names)]).to_csv(tmp_path / "sensitivity.csv", index=False)
    pd.DataFrame([dict(street=s, year=y, houses=n, variation=v, peak_loading_pct=0.0, at_risk_thermal=i == 2,
                       watch_thermal=i == 1, life_days_per_year=1.0, max_hot_spot_c=1.0)
                  for s in run_study.STREETS for y in sensitivity.YEARS for v in names
                  for i, n in enumerate(run_study.STREETS[s]["houses"])]).to_csv(
        tmp_path / "sensitivity_limits.csv", index=False)
    assert figures.main() == 0
    assert {p.name for p in (tmp_path / "fig").glob("*.png")} == {
        "first_limits.png", "life_vs_houses.png", "paper_vs_real.png", "sensitivity.png", "sensitivity_limits.png",
        "winters.png"}
    by_var = pd.read_csv(tmp_path / "summary_sensitivity_limits.csv")
    assert len(by_var) == 3 * 3 * 16
    assert np.allclose(by_var.at_risk_vs_rule80, by_var.at_risk / by_var.rule80)
    pd.read_csv(tmp_path / "runs.csv").iloc[:-1].to_csv(tmp_path / "runs.csv", index=False)  # one run missing
    with pytest.raises(ValueError, match="planned grid"):
        figures.main()


# ---------------------------------------------------------------- weather years
@pytest.mark.parametrize("year", run_study.YEARS)
def test_weather_year_is_an_exact_hourly_swedish_calendar_year(year):
    wx = run_study.weather(year)
    local = wx.index.tz_convert("Europe/Stockholm")
    assert local[0] == pd.Timestamp(f"{year}-01-01 00:00", tz="Europe/Stockholm")
    assert local[-1] == pd.Timestamp(f"{year}-12-31 23:00", tz="Europe/Stockholm")
    assert len(wx) == (8784 if calendar.isleap(year) else 8760)
    assert wx.index.is_unique and (wx.index.to_series().diff().dropna() == pd.Timedelta("1h")).all()
    assert list(wx.columns) == ["temp_c", "pv_kw_per_kwp"] and not wx.isna().any().any()
    assert (wx.pv_kw_per_kwp >= 0).all() and wx.temp_c.between(-40, 40).all()


def test_study_2023_is_the_programs_own_weather_and_the_named_years_are_coldest_and_mildest():
    from hotspotlv.paths import package_data
    a = run_study.weather(2023)
    b = pd.read_csv(package_data() / "weather_vasteras_2023.csv", index_col=0, parse_dates=True)
    pd.testing.assert_frame_equal(a, b.loc[a.index])
    cold = {y: int((run_study.weather(y).temp_c < -10).sum()) for y in run_study.YEARS}
    assert max(cold, key=cold.get) == 2010 and min(cold, key=cold.get) == 2020
    assert 2016 not in run_study.YEARS and 2019 not in run_study.YEARS       # SMHI gaps over 6 h


# ---------------------------------------------------------------- 315 kVA in the thermal model; the variations
def test_the_315_kva_unit_in_the_thermal_model():
    from hotspotlv.study import enclosure_note
    from hotspotlv.thermal import kiosk_extra_top_oil_rise_k
    p = ThermalParams.for_transformer(315)
    assert kiosk_extra_top_oil_rise_k(315) == pytest.approx((10 + 5 * 65 / 250) / 2)     # Table 6, 250–500 kVA
    assert p.R == pytest.approx(2800 / 324) and p.d_theta_or == pytest.approx(55 + 5.65) and p.enclosure == "kiosk"
    assert "table covers" not in enclosure_note(p, 315)


@pytest.mark.parametrize("kva", [315, 500, 800])
def test_each_sensitivity_variation_moves_one_thing(kva):
    import sensitivity
    base = ThermalParams.for_transformer(kva)
    vs = list(sensitivity.variations(base))
    assert len({n for n, _, _ in vs}) == 16 and vs[0][1] == base and vs[0][2] == 0
    for name, p, dt in vs[1:]:
        moved = {f.name for f in dataclasses.fields(p) if getattr(p, f.name) != getattr(base, f.name)}
        assert (len(moved) == 1 and dt == 0) or (not moved and abs(dt) == 5) or "kiosk" in name or "outdoor" in name, name
    outdoor = {n: p for n, p, _ in vs}["outdoor unit (no kiosk rise)"]
    assert outdoor == ThermalParams.for_transformer(kva, enclosure="outdoor")
