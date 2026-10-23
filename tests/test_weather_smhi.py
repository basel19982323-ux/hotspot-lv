"""The SMHI temperature path without the network: a fake SMHI answers, and every case of the packaging review
round 2 is pinned — a 2023 file in Swedish time offline, a recent file through SMHI's latest months, data that run
into the future, an answer that is not SMHI's file, a wrong station, a cut connection, a stale archive, and that
nothing is fetched twice in one run."""

import http.client
import io
import os
import time
import urllib.error

import numpy as np
import pandas as pd
import pytest

import hotspotlv.weather as w
from hotspotlv.station import ambient_for, assess_station, read_station_csv


def smhi_csv(station: int, start: str, end: str, period_text: str = "Kvalitetskontrollerade historiska data") -> bytes:
    """A file in SMHI's layout: station block, parameter block, the data table with quality codes."""
    t = pd.date_range(start, end, freq="h", tz="UTC")
    temp = 5 + 10 * np.sin(np.arange(len(t)) / 24 * 2 * np.pi)
    rows = [f"{x:%Y-%m-%d};{x:%H:%M:%S};{v:.1f};G" for x, v in zip(t, temp)]
    rows[0] += f";;{period_text}"                                         # SMHI writes the period beside the data
    table = "\n".join(rows)
    return ("﻿Stationsnamn;Stationsnummer;Stationsnät;Mäthöjd (meter över marken)\n"
            f"Test;{station};SMHIs stationsnät;2.0\n\n"
            "Parameternamn;Beskrivning;Enhet\nLufttemperatur;momentanvärde, 1 gång/tim;celsius\n\n"
            f"Datum;Tid (UTC);Lufttemperatur;Kvalitet;;Tidsutsnitt:\n{table}\n").encode("utf-8")


class FakeSMHI:
    """Stands in for urllib.request.urlopen: answers per period, counts every request."""

    def __init__(self, answers: dict):
        self.answers, self.requests = answers, []

    def __call__(self, url, timeout=None):
        period = url.split("/period/")[1].split("/")[0]
        self.requests.append(period)
        answer = self.answers.get(period, urllib.error.HTTPError(url, 404, "Not Found", None, None))
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An empty cache, downloads allowed, and the module's per-run memory cleared."""
    monkeypatch.setenv("HOTSPOTLV_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("HOTSPOTLV_OFFLINE", raising=False)
    monkeypatch.setattr(w, "_FETCHED", set())
    monkeypatch.setattr(w, "_NOT_FOUND", set())
    monkeypatch.setattr(w, "_PARSED", {})
    return tmp_path / "cache"


def fake(monkeypatch, **answers):
    smhi = FakeSMHI({k.replace("_", "-"): v for k, v in answers.items()})
    monkeypatch.setattr(w.urllib.request, "urlopen", smhi)
    return smhi


def utc_hours(start, end):
    return pd.date_range(start, end, freq="h", tz="UTC")


def test_a_2023_file_in_swedish_time_needs_no_download(cache, monkeypatch):
    """Round 2, build HIGH: 00:00 Swedish time on 1 Jan 2023 is 23:00 UTC on 31 Dec 2022 — the whole-year rule asked
    for 2022 and downloaded 7 MB (offline: failed). The shipped file now holds the Swedish calendar year (numbers
    audit of 23 Oct 2026), so that hour is SMHI's own 2.4 °C — offline, nothing written, nothing filled."""
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    loc = utc_hours("2022-12-31 23:00", "2023-12-31 22:00").tz_convert("Europe/Stockholm")
    text = "time;kW\n" + "\n".join(f"{t:%Y-%m-%d %H:%M};150" for t in loc)
    st = read_station_csv(io.StringIO(text))
    assert st.index[0] == pd.Timestamp("2022-12-31 23:00", tz="UTC")
    assert st.period() == "2023-01-01 – 2023-12-31 (Swedish time, whole days)"
    amb = ambient_for(st.index)
    assert amb.attrs["missing_hours_filled"] == 0 and amb.attrs["smhi_latest_from"] is None
    assert amb.iloc[0] == 2.4                                             # SMHI 96190, 2022-12-31 23:00 UTC
    verdict = assess_station(st, 500, amb).verdict
    assert verdict.level in ("OK", "Watch", "At risk")
    assert not any(n.startswith("outdoor temperature:") for n in verdict.notes)
    assert not cache.exists()


def test_a_few_hours_past_the_shipped_year_are_held_and_noted(cache, monkeypatch):
    """The short-gap rule still covers the edge: a file running 3 h into 2024 (UTC) needs no download offline."""
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    hours = utc_hours("2023-01-01 00:00", "2024-01-01 02:00")
    text = "time;kW\n" + "\n".join(f"{t:%Y-%m-%dT%H:%M}Z;150" for t in hours)
    st = read_station_csv(io.StringIO(text))
    amb = ambient_for(st.index)
    assert amb.attrs["missing_hours_filled"] == 3 and amb.iloc[-1] == amb.iloc[-4]   # held at 2023-12-31 23:00 UTC
    notes = assess_station(st, 500, amb).verdict.notes
    assert any(n.startswith("outdoor temperature: 3 hours with no value in the temperature data used") for n in notes)
    assert not cache.exists()


def test_a_recent_file_uses_the_latest_months_and_says_so(cache, monkeypatch):
    """Round 2, distribution HIGH: SMHI's checked archive lags three months, so a file of the last twelve months
    always failed ("a gap of 4409 h"). The latest-months file covers the rest; the verdict notes name the hour."""
    smhi = fake(monkeypatch, corrected_archive=smhi_csv(96190, "2024-01-01", "2026-07-01 06:00"),
                latest_months=smhi_csv(96190, "2026-06-14 01:00", "2026-10-22 21:00", "Data från senaste fyra månaderna"))
    hours = utc_hours("2025-10-23 00:00", "2026-10-21 23:00")
    text = "time;kW\n" + "\n".join(f"{t:%Y-%m-%dT%H:%M}Z;150" for t in hours)
    st = read_station_csv(io.StringIO(text))
    amb = ambient_for(st.index)
    assert amb.attrs["smhi_latest_from"] == pd.Timestamp("2026-07-01 07:00", tz="UTC")
    assert not amb.isna().any()
    notes = assess_station(st, 500, amb).verdict.notes
    assert any("2026-07-01 07:00 UTC" in n and "latest-months" in n for n in notes)
    assert smhi.requests == ["corrected-archive", "latest-months"]
    ambient_for(st.index)                                                 # the same run asks again …
    assert smhi.requests == ["corrected-archive", "latest-months"]        # … and nothing is downloaded again
    assert sorted(p.name for p in (cache / "raw").iterdir()) == ["smhi_96190_latest.csv", "smhi_96190_temp.csv"]


def test_a_file_that_runs_past_smhi_says_until_when(cache, monkeypatch):
    smhi = fake(monkeypatch, corrected_archive=smhi_csv(96190, "2024-01-01", "2026-07-01 06:00"),
                latest_months=smhi_csv(96190, "2026-06-14 01:00", "2026-10-22 21:00"))
    with pytest.raises(ValueError, match=r"has temperatures up to 2026-10-22 21:00 UTC; the data run to "
                                         r"2026-12-31 23:00 UTC"):
        w.load_smhi_hours(utc_hours("2026-09-01", "2026-12-31 23:00"))
    assert smhi.requests == ["corrected-archive", "latest-months"]       # the latest months once, not on every call
    with pytest.raises(ValueError):
        w.load_smhi_hours(utc_hours("2026-09-01", "2026-12-31 23:00"))
    assert smhi.requests == ["corrected-archive", "latest-months"]


def test_a_long_gap_inside_the_data_names_its_dates(cache, monkeypatch):
    data = smhi_csv(97400, "2024-01-01", "2024-03-31 23:00").decode("utf-8").splitlines()
    data = [r for r in data if not r.startswith("2024-02-10;")]           # one whole day missing at SMHI
    fake(monkeypatch, corrected_archive="\n".join(data).encode("utf-8"))
    with pytest.raises(ValueError, match=r"no temperatures from 2024-02-10 00:00 to 2024-02-10 23:00 UTC \(24 h"):
        w.load_smhi_hours(utc_hours("2024-01-15", "2024-03-15"), 97400)
    s = w.load_smhi_hours(utc_hours("2024-01-01", "2024-02-05"), 97400)   # a period before the gap is fine
    assert not s.isna().any() and s.attrs["missing_hours_filled"] == 0


def test_an_answer_that_is_not_smhis_file_is_not_saved(cache, monkeypatch):
    """Round 2, build MEDIUM: a Wi-Fi login page (HTTP 200) was saved as the archive and every later assessment
    said "StopIteration: ". Now it is refused before saving, with a sentence about the network."""
    fake(monkeypatch, corrected_archive=b"<html><body>Please log in to the hotel Wi-Fi</body></html>")
    with pytest.raises(ConnectionError, match="Wi-Fi login page or a proxy"):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 97400)
    assert not (cache / "raw").exists() or not list((cache / "raw").iterdir())


def test_a_bad_saved_file_is_removed_and_explained(cache, monkeypatch):
    raw = cache / "raw"
    raw.mkdir(parents=True)
    (raw / "smhi_97400_temp.csv").write_text("<html>login</html>", encoding="utf-8")   # saved by an older version
    fake(monkeypatch)
    with pytest.raises(ValueError, match="was not SMHI's temperature file and has been deleted"):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 97400)
    assert not (raw / "smhi_97400_temp.csv").exists()


def test_a_wrong_station_and_a_cut_connection_say_what_happened(cache, monkeypatch):
    not_found = urllib.error.HTTPError("u", 404, "Not Found", None, None)
    smhi = fake(monkeypatch, corrected_archive=not_found)
    with pytest.raises(w.SmhiNotFound, match="no hourly air temperatures for station 999999 — check the station"):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 999999)
    with pytest.raises(w.SmhiNotFound):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 999999)
    assert smhi.requests == ["corrected-archive"]                          # a 404 is remembered for the run
    fake(monkeypatch, corrected_archive=http.client.IncompleteRead(b"x" * 66, 6_999_934))
    with pytest.raises(ConnectionError, match=r"The download failed \(IncompleteRead\)") as e:
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 97400)
    assert "Only the 2023 temperatures of station 96190" in str(e.value)
    assert not list(cache.rglob("*.part"))


def test_a_stale_archive_is_refreshed_once_a_fresh_one_is_not(cache, monkeypatch):
    raw = cache / "raw"
    raw.mkdir(parents=True)
    old = raw / "smhi_97400_temp.csv"
    old.write_bytes(smhi_csv(97400, "2024-01-01", "2025-10-31 23:00"))
    smhi = fake(monkeypatch, corrected_archive=smhi_csv(97400, "2024-01-01", "2026-07-01 06:00"))
    hours = utc_hours("2025-11-01", "2025-12-31 23:00")
    os.utime(old, (time.time() - 2 * 3600, time.time() - 2 * 3600))        # two hours old: kept
    with pytest.raises(ValueError, match="has temperatures up to 2025-10-31 23:00 UTC"):   # this fake has no
        w.load_smhi_hours(hours, 97400)                                    # latest months (404) …
    assert smhi.requests == ["latest-months"]                              # … and the young archive was not re-fetched
    week = w.ARCHIVE_REFRESH_AFTER_H * 3600 + 60
    os.utime(old, (time.time() - week, time.time() - week))                # older than the refresh age: fetched again
    smhi.requests.clear()
    s = w.load_smhi_hours(hours, 97400)
    assert smhi.requests == ["corrected-archive"] and not s.isna().any()


def test_offline_writes_nothing_and_says_so(cache, monkeypatch):
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    fake(monkeypatch)
    with pytest.raises(ConnectionError, match="Downloads are switched off"):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 97400)
    with pytest.raises(ConnectionError, match="PVGIS"):
        w.load_pvgis_pv(2022)
    assert not cache.exists()                                              # not even an empty raw folder


# ------------------------------------------------------------------ round 3 (23 Oct 2026)
def _save(cache, station, period, data: bytes, age_h: float = 0.0):
    raw = cache / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / w._FILE[period].format(station=station)
    path.write_bytes(data)
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))
    return path


@pytest.mark.parametrize("gap, ok", [(6, True), (7, False)])
def test_six_hours_are_filled_seven_are_an_error_inside_and_at_the_start(cache, monkeypatch, gap, ok):
    rows = smhi_csv(97400, "2024-01-01", "2024-03-31 23:00").decode("utf-8").splitlines()
    cut = {f"2024-02-10;{h:02d}:00:00" for h in range(gap)}
    rows = [r for r in rows if r[:19] not in cut]
    fake(monkeypatch, corrected_archive="\n".join(rows).encode("utf-8"))
    inside = utc_hours("2024-02-01", "2024-02-20")
    start = utc_hours(f"{pd.Timestamp('2024-01-01') - pd.Timedelta(hours=gap):%Y-%m-%d %H:%M}", "2024-01-05")
    for hours in (inside, start):
        if ok:
            s = w.load_smhi_hours(hours, 97400)
            assert not s.isna().any() and s.attrs["missing_hours_filled"] == gap
        else:
            with pytest.raises(ValueError, match=f"{gap} h|the data start at"):
                w.load_smhi_hours(hours, 97400)


def test_smhis_file_for_another_station_is_not_saved(cache, monkeypatch):
    fake(monkeypatch, corrected_archive=smhi_csv(97400, "2024-01-01", "2024-03-31 23:00"))
    with pytest.raises(ConnectionError, match="not with the temperature file of this station"):
        w.load_smhi_hours(utc_hours("2024-01-01", "2024-01-31"), 97510)
    assert not (cache / "raw").exists() or not list((cache / "raw").iterdir())


def test_a_failed_refresh_keeps_the_saved_latest_months(cache, monkeypatch):
    """Round 3: offline, a file ending 5 h after the saved latest months failed although the same file had passed
    online — the saved copy was thrown away when its refresh failed."""
    _save(cache, 96190, w.ARCHIVE, smhi_csv(96190, "2024-01-01", "2026-07-01 06:00"))
    _save(cache, 96190, w.LATEST, smhi_csv(96190, "2026-06-14 01:00", "2026-10-22 21:00"))
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    s = w.load_smhi_hours(utc_hours("2026-09-01", "2026-10-23 02:00"), 96190)     # 5 h past the saved copy: held
    assert not s.isna().any() and s.attrs["smhi_latest_from"] is not None
    with pytest.raises(ConnectionError, match=r"^The data need SMHI temperatures of station 96190 for 2026-10-22 "
                                              r"22:00 to 2026-10-23 04:00 UTC.*Downloads are switched off"):
        w.load_smhi_hours(utc_hours("2026-09-01", "2026-10-23 04:00"), 96190)  # 7 h past: says what is missing


def test_an_old_archive_is_not_fetched_again_while_the_latest_months_cover(cache, monkeypatch):
    """Round 3: every recent file downloaded the 7 MB archive again once a week, byte for byte the same."""
    _save(cache, 96190, w.ARCHIVE, smhi_csv(96190, "2024-01-01", "2026-07-01 06:00"), age_h=30 * 24)
    _save(cache, 96190, w.LATEST, smhi_csv(96190, "2026-06-14 01:00", "2026-10-22 21:00"))
    smhi = fake(monkeypatch)
    s = w.load_smhi_hours(utc_hours("2025-10-23", "2026-10-21 23:00"), 96190)
    assert smhi.requests == [] and not s.isna().any()


def test_a_gap_between_an_old_archive_and_the_latest_months_fetches_the_archive_or_says_why(cache, monkeypatch):
    _save(cache, 96190, w.ARCHIVE, smhi_csv(96190, "2024-01-01", "2026-03-01 00:00"), age_h=60 * 24)
    _save(cache, 96190, w.LATEST, smhi_csv(96190, "2026-06-14 01:00", "2026-10-22 21:00"))
    hours = utc_hours("2026-02-01", "2026-07-01")
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    with pytest.raises(ConnectionError, match="The data need SMHI temperatures .* 2026-03-01 01:00 to 2026-06-14 "
                                              "00:00 UTC"):                  # not "choose another SMHI station"
        w.load_smhi_hours(hours, 96190)
    monkeypatch.delenv("HOTSPOTLV_OFFLINE")
    smhi = fake(monkeypatch, corrected_archive=smhi_csv(96190, "2024-01-01", "2026-07-01 06:00"))
    s = w.load_smhi_hours(hours, 96190)
    assert smhi.requests == ["corrected-archive"] and not s.isna().any()


def test_a_2023_file_that_runs_into_2024_names_the_hours_outside_the_shipped_year(cache, monkeypatch):
    monkeypatch.setenv("HOTSPOTLV_OFFLINE", "1")
    with pytest.raises(ConnectionError, match=r"^The data need SMHI temperatures of station 96190 for 2024-01-01 "
                                              r"00:00 to 2024-01-01 23:00 UTC"):
        w.load_smhi_hours(utc_hours("2023-01-01", "2024-01-01 23:00"), 96190)


def test_two_callers_at_once_download_once(cache, monkeypatch):
    import threading
    smhi = fake(monkeypatch, corrected_archive=smhi_csv(97400, "2024-01-01", "2024-03-31 23:00"))
    slow = smhi.__call__

    def answer(url, timeout=None):
        time.sleep(0.3)                                  # both callers are inside fetch at the same time
        return slow(url, timeout)
    monkeypatch.setattr(w.urllib.request, "urlopen", answer)
    errors = []

    def call():
        try:
            w.fetch_smhi_temperature(97400)
        except Exception as e:                           # noqa: BLE001 — collected for the assertion
            errors.append(e)
    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and smhi.requests == ["corrected-archive"]
    assert [p.name for p in (cache / "raw").iterdir()] == ["smhi_97400_temp.csv"]   # no .part left
