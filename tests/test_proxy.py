"""Fast mode must reproduce the full power flow closely — checked on a 10-day slice."""

import numpy as np
import pytest

from hotspotlv.feeder import build_feeder
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.proxy import fit_proxy
from hotspotlv.timeseries import run_year
from hotspotlv.weather import get_weather


@pytest.fixture(scope="module")
def case():
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=30, ev_share=0.6, pv_share=0.5, seed=7), wx)
    # 5 winter days + 5 summer days: covers heavy load and heavy export
    hours = nb.cons_kw.loc["2023-12-04":"2023-12-08"].index.append(nb.cons_kw.loc["2023-06-10":"2023-06-14"].index)
    cons, gen = nb.cons_kw.loc[hours], nb.gen_kw.loc[hours]
    proxy = fit_proxy(build_feeder(n_houses=30), cons, gen, n_samples=60)
    full = run_year(build_feeder(n_houses=30), cons, gen)
    return proxy, proxy.evaluate(cons, gen), full


def test_proxy_matches_full_power_flow(case):
    proxy, fast, full = case
    d = (fast.hourly - full.hourly).abs()
    assert d["loading_pct"].max() < 0.5                     # percentage points of nameplate
    assert d["s_kva"].max() / full.hourly["s_kva"].max() < 0.01
    assert d["vmin_pu"].max() < 0.01                       # voltage depends on WHICH houses load; one curve cannot know
    assert d["vmax_pu"].max() < 0.01
    assert d["line_max_pct"].max() < 5
    assert fast.hours_above == full.hours_above


def test_proxy_reports_its_fit_error(case):
    proxy, _, _ = case
    assert proxy.n_samples >= 60
    assert proxy.fit_error["loading_pct_max_abs"] < 0.5        # percentage points of nameplate on the fit sample
    assert proxy.qloss_coef > 0                              # reactive losses grow with S²
    assert proxy.loss_coef[0] > 0                           # losses grow with P²


def test_proxy_rejects_wrong_house_count(case):
    proxy, _, _ = case
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=10, seed=1), wx)
    with pytest.raises(ValueError):
        fit_proxy(build_feeder(n_houses=30), nb.cons_kw.iloc[:24], nb.gen_kw.iloc[:24])
