"""Mode C: a fleet index or a long CSV → ranked table, with one broken station not stopping the run."""

import pandas as pd
import pytest

from hotspotlv.fleet import (FleetStation, export_fleet_table, read_fleet_index, read_fleet_long,
                             screen_fleet)
from hotspotlv.paths import package_data

EXAMPLES = package_data() / "examples"


@pytest.fixture(scope="module")
def example_run():
    stations = read_fleet_index(EXAMPLES / "fleet_index.csv")
    seen = []
    table, results = screen_fleet(stations, progress=lambda i, n, sid: seen.append((i, n, sid)))
    return stations, table, results, seen


def test_index_is_read(example_run):
    stations, _, _, _ = example_run
    assert [s.station_id for s in stations] == ["NS-101 Lövängen", "NS-102 Björkbacken", "NS-103 Tallhöjden"]
    assert [s.rated_kva for s in stations] == [500, 500, 800]
    assert all(s.file.exists() for s in stations) and stations[0].smhi_station == 96190


def test_ranked_table(example_run):
    _, table, results, seen = example_run
    assert list(table.index) == [1, 2, 3] and table.index.name == "rank"
    assert set(results) == set(table["station_id"])
    ranks = table["verdict"].map({"At risk": 0, "Watch": 1, "OK": 2}).tolist()
    assert ranks == sorted(ranks)                                       # worst first
    assert (table["error"] == "").all()
    assert seen[-1][:2] == (3, 3)                                       # progress callback reached the end
    # the 80-house 500 kVA station must rank above the 45-house one
    pos = {sid: r for r, sid in table["station_id"].items()}
    assert pos["NS-102 Björkbacken"] < pos["NS-101 Lövängen"]
    assert table.loc[pos["NS-102 Björkbacken"], "peak_loading_pct"] > 80


def test_export(example_run, tmp_path):
    _, table, _, _ = example_run
    out = export_fleet_table(table, tmp_path / "fleet.csv")
    back = pd.read_csv(out, index_col="rank")
    assert list(back.columns) == list(table.columns) and len(back) == 3


def test_bad_station_is_reported_not_raised(tmp_path):
    (tmp_path / "bad.csv").write_text("timestamp,kw\n2023-01-01 00:00,1\n2023-01-01 02:00,2\n2023-01-02 00:00,3\n")
    good = EXAMPLES / "ns-101.csv"
    stations = [FleetStation("good", 500, file=good), FleetStation("bad", 500, file=tmp_path / "bad.csv")]
    table, results = screen_fleet(stations)
    assert set(results) == {"good"}
    bad_row = table[table["station_id"] == "bad"].iloc[0]
    assert bad_row["verdict"] == "error" and bad_row["error"] != ""
    assert table.iloc[-1]["station_id"] == "bad"                        # errors sort last


def test_long_csv(tmp_path):
    a = pd.read_csv(EXAMPLES / "ns-101.csv").iloc[:24 * 10]
    b = pd.read_csv(EXAMPLES / "ns-102.csv").iloc[:24 * 10]
    long = pd.concat([a.assign(station_id="A"), b.assign(station_id="B")])
    long.to_csv(tmp_path / "long.csv", index=False)
    stations = read_fleet_long(tmp_path / "long.csv", ratings={"A": 500, "B": 500})
    assert [s.station_id for s in stations] == ["A", "B"] and all(s.series is not None for s in stations)
    table, _ = screen_fleet(stations)
    assert len(table) == 2 and (table["error"] == "").all()
    with pytest.raises(ValueError, match="rated_kva"):
        read_fleet_long(tmp_path / "long.csv", ratings={"A": 500})


def test_fleet_index_validation(tmp_path):
    (tmp_path / "idx.csv").write_text("station_id,file\nX,x.csv\n")
    with pytest.raises(ValueError, match="rated_kva"):
        read_fleet_index(tmp_path / "idx.csv")
