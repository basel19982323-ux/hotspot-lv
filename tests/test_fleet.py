"""Mode C: a fleet index or a long CSV → ranked table, notes carried, one broken station not stopping the run."""

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
    assert [s.rated_kva for s in stations] == [500, 500, 800]
    assert all(s.file.exists() for s in stations) and stations[0].smhi_station == 96190
    assert all("synthetic" in s.station_id.lower() for s in stations)


def test_ranked_table(example_run):
    _, table, results, seen = example_run
    assert list(table.index) == [1, 2, 3] and table.index.name == "rank"
    assert set(results) == set(table["station_id"])
    ranks = table["verdict"].map({"At risk": 0, "Watch": 1, "OK": 2}).tolist()
    assert ranks == sorted(ranks)                                       # worst first
    assert (table["error"] == "").all()
    assert seen[-1][:2] == (3, 3)                                       # progress callback reached the end
    pos = {sid: r for r, sid in table["station_id"].items()}
    big = next(s for s in pos if "102" in s); small = next(s for s in pos if "101" in s)
    assert pos[big] < pos[small]                                        # the 80-house 500 kVA station ranks above the 45-house one
    assert table.loc[pos[big], "peak_loading_pct"] > 80
    assert table["why"].str.contains("not checked").all()               # the caveat survives into the table
    assert table["notes"].str.contains("SMHI station 96190").all() and (table["smhi_station"] == 96190).all()
    assert table["period"].str.endswith("(local)").all()


def test_export(example_run, tmp_path):
    _, table, _, _ = example_run
    out = export_fleet_table(table, tmp_path / "fleet.csv")
    back = pd.read_csv(out, index_col="rank", encoding="utf-8-sig")
    assert list(back.columns) == list(table.columns) and len(back) == 3
    assert back["why"].str.contains("not checked").all()


def test_bad_station_is_reported_not_raised(tmp_path):
    (tmp_path / "bad.csv").write_text("timestamp,kw\n2023-01-01 00:00,1\n2023-01-01 02:00,2\n2023-01-02 00:00,3\n")
    good = EXAMPLES / "ns-101.csv"
    stations = [FleetStation("good", 500, file=good), FleetStation("bad", 500, file=tmp_path / "bad.csv")]
    def boom(*a):
        raise RuntimeError("progress bar died")
    table, results = screen_fleet(stations, progress=boom)               # a broken callback must not stop it either
    assert set(results) == {"good"}
    bad_row = table[table["station_id"] == "bad"].iloc[0]
    assert bad_row["verdict"] == "error" and bad_row["error"] != ""
    assert table.iloc[-1]["station_id"] == "bad"                        # errors sort last
    assert "the default; choose the SMHI station" in table.iloc[0]["notes"]    # no smhi_station given → said so


def test_long_csv(tmp_path):
    a = pd.read_csv(EXAMPLES / "ns-101.csv").iloc[:24 * 10]
    b = pd.read_csv(EXAMPLES / "ns-102.csv").iloc[:24 * 10]
    long = pd.concat([a.assign(station_id="101"), b.assign(station_id="NS/2 Björk")])
    long.to_csv(tmp_path / "long.csv", index=False, encoding="utf-8-sig")
    stations = read_fleet_long(tmp_path / "long.csv", ratings={101: 500, "NS/2 Björk": "500 kVA"})
    assert [s.station_id for s in stations] == ["101", "NS/2 Björk"] and all(s.series is not None for s in stations)
    assert [s.rated_kva for s in stations] == [500.0, 500.0]
    table, _ = screen_fleet(stations)
    assert len(table) == 2 and (table["error"] == "").all()
    assert not list(tmp_path.glob(".long__*"))                          # no temp files left behind
    with pytest.raises(ValueError, match="rated_kva"):
        read_fleet_long(tmp_path / "long.csv", ratings={"101": 500})
    # one unreadable station in a long CSV becomes an error row, the others are screened
    bad = pd.concat([a.assign(station_id="A"), pd.DataFrame({"timestamp": ["2023-01-01 00:00", "2023-01-01 02:00", "2023-01-02 00:00"],
                                                             "p_kw": [1, 2, 3], "station_id": "B"})])
    bad.to_csv(tmp_path / "bad.csv", index=False)
    stations = read_fleet_long(tmp_path / "bad.csv", ratings={"A": 500, "B": 500})
    assert [s.error is None for s in stations] == [True, False]
    table, results = screen_fleet(stations)
    assert set(results) == {"A"} and table.iloc[-1]["verdict"] == "error"


def test_fleet_index_validation(tmp_path):
    (tmp_path / "idx.csv").write_text("station_id,file\nX,x.csv\n")
    with pytest.raises(ValueError, match="rated_kva"):
        read_fleet_index(tmp_path / "idx.csv")
    (tmp_path / "dup.csv").write_text("station_id,rated_kva,file\nX,500,x.csv\nX,500,y.csv\n")
    with pytest.raises(ValueError, match="duplicate"):
        read_fleet_index(tmp_path / "dup.csv")
    (tmp_path / "kva.csv").write_text("station_id,rated_kva,file,smhi_station\nX,500 kVA,x.csv,\n")
    s = read_fleet_index(tmp_path / "kva.csv")[0]
    assert s.rated_kva == 500.0 and s.smhi_station is None
    (tmp_path / "sp.csv").write_text("station_id,rated_kva,file\nX,1 250,x.csv\n", encoding="cp1252")
    assert read_fleet_index(tmp_path / "sp.csv")[0].rated_kva == 1250.0
    with pytest.raises(ValueError, match="duplicate"):
        screen_fleet([FleetStation("X", 500, file=EXAMPLES / "ns-101.csv")] * 2)
