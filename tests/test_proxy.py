"""Fast mode must reproduce the full power flow closely on a 10-day slice, generalise to
another seed on the same feeder, and hand hours near a limit back to the exact power flow."""

import numpy as np
import pytest

from hotspotlv.feeder import build_feeder
from hotspotlv.neighbourhood import Scenario, build_neighbourhood
from hotspotlv.proxy import fit_proxy, refine_near_limits
from hotspotlv.timeseries import run_year
from hotspotlv.weather import get_weather


def _slice(nb):
    # 5 winter days + 5 summer days: covers heavy load and heavy export
    hours = nb.cons_kw.loc["2023-12-04":"2023-12-08"].index.append(nb.cons_kw.loc["2023-06-10":"2023-06-14"].index)
    return nb.cons_kw.loc[hours], nb.gen_kw.loc[hours]


@pytest.fixture(scope="module")
def case():
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=30, ev_share=0.6, pv_share=0.5, seed=7), wx)
    cons, gen = _slice(nb)
    proxy = fit_proxy(build_feeder(n_houses=30), cons, gen, n_samples=60)
    full = run_year(build_feeder(n_houses=30), cons, gen)
    return proxy, proxy.evaluate(cons, gen), full, cons, gen


def test_proxy_matches_full_power_flow(case):
    proxy, fast, full, _, _ = case
    d = (fast.hourly - full.hourly).abs()
    assert d["loading_pct"].max() < 0.5                     # percentage points of nameplate
    assert d["s_kva"].max() / full.hourly["s_kva"].max() < 0.01
    assert d["p_kw"].max() < 2.0                            # same LV-side convention as run_year
    assert d["p_kw"].mean() < 0.3                           # adding the transformer's losses to p_kw would show here
    assert d["vmin_pu"].max() < 0.01                        # voltage depends on WHICH houses load; one curve cannot know
    assert d["vmax_pu"].max() < 0.01
    assert d["line_max_pct"].max() < 5
    assert fast.hours_above == full.hours_above


def test_proxy_fit_and_structure(case):
    proxy, _, _, _, _ = case
    assert proxy.n_samples >= 60
    assert proxy.fit_error["loading_pct_max_abs"] < 0.5      # percentage points of nameplate on the fit sample
    assert proxy.fit_error["vmin_max_abs"] < 0.005
    assert proxy.fit_error["cable_pct_max_abs"] < 2
    assert proxy.qloss_coef > 0                              # reactive losses grow with S²
    assert proxy.loss_coef[0] > 0 and proxy.cable_loss_coef[0] > 0
    assert list(proxy.feeder_of_house) == [0] * 10 + [1] * 10 + [2] * 10   # 30 houses on 3 feeders, in order


def test_proxy_generalises_to_another_seed(case):
    proxy, _, _, _, _ = case                                 # fitted on seed 7
    wx = get_weather(2023)
    other = build_neighbourhood(Scenario(n_houses=30, ev_share=0.6, pv_share=0.5, seed=8), wx)
    cons, gen = _slice(other)
    fast = proxy.evaluate(cons, gen)
    full = run_year(build_feeder(n_houses=30), cons, gen)
    d = (fast.hourly - full.hourly).abs()
    assert d["loading_pct"].max() < 0.7
    assert d["vmin_pu"].max() < 0.012


def test_refine_near_limits_restores_exact_rows():
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=90, ev_share=0.7, pv_share=0.3, seed=3), wx)
    cons, gen = _slice(nb)
    net = build_feeder(n_houses=90)
    proxy = fit_proxy(net, cons, gen, n_samples=60)
    fast = proxy.evaluate(cons, gen)
    refined, n = refine_near_limits(net, fast, cons, gen)
    full = run_year(build_feeder(n_houses=90), cons, gen)
    assert n > 0
    near = ((full.hourly["loading_pct"] > 90) | (full.hourly["vmin_pu"] < 0.94)
            | (full.hourly["vmax_pu"] > 1.04) | (full.hourly["line_max_pct"] > 90))
    d = (refined.hourly - full.hourly).abs()
    assert d.loc[near, "loading_pct"].max() < 1e-6            # exact where it matters
    assert d.loc[near, "vmin_pu"].max() < 1e-9
    assert d.loc[near, "p_kw"].max() < 1e-6                   # every column is replaced, not only the loading
    assert d.loc[near, "vmax_pu"].max() < 1e-9
    with pytest.raises(ValueError):
        refine_near_limits(net, fast, cons.iloc[:-1], gen.iloc[:-1])   # index mismatch
    assert d["loading_pct"].max() <= (fast.hourly - full.hourly).abs()["loading_pct"].max() + 1e-9


def test_proxy_validation(case):
    proxy, _, _, cons, gen = case
    wx = get_weather(2023)
    nb = build_neighbourhood(Scenario(n_houses=10, seed=1), wx)
    with pytest.raises(ValueError):
        fit_proxy(build_feeder(n_houses=30), nb.cons_kw.iloc[:24], nb.gen_kw.iloc[:24])     # wrong house count
    with pytest.raises(ValueError):
        fit_proxy(build_feeder(n_houses=30), cons, gen, n_samples=3)
    with pytest.raises(ValueError):
        proxy.evaluate(cons, gen.iloc[:, :1])                                              # gen shape mismatch
    bad = cons.copy(); bad.iloc[0, 0] = np.nan
    with pytest.raises(ValueError):
        proxy.evaluate(bad, gen)
    flat = cons.copy(); flat[:] = 1.0
    with pytest.raises(ValueError):
        fit_proxy(build_feeder(n_houses=30), flat, gen * 0)                                 # nothing to fit
