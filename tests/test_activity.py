"""Activity tests: golden files (skipped if data/raw/ is empty) and synthetic files."""

import time
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fitwriter import FitWriter

import stridelab as sl
from stridelab.__main__ import main
from stridelab.reader import FIT_EPOCH

T0 = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


def fit_time(dt: datetime) -> int:
    return int(dt.timestamp()) - FIT_EPOCH


# ── golden files ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def long_run(request):
    path = request.getfixturevalue("fit_file")("long_run")
    return sl.read(path)


def test_golden_read_is_fast(fit_file):
    path = fit_file("long_run")
    start = time.perf_counter()
    act = sl.read(path)
    act.records_df()
    assert (
        time.perf_counter() - start < 1.0
    )  # CLAUDE.md's threshold for considering a compiled core


def test_golden_records_df(long_run):
    df = long_run.records_df()
    assert len(df) == 9825
    assert str(df.index.tz) == "UTC" and df.index.is_monotonic_increasing
    gaps = df.index.to_series().diff().dt.total_seconds()
    assert (gaps > 1).sum() == 4 and gaps.max() == 139  # the four timer pauses
    assert df["elapsed_s"].iloc[0] == 0 and df["elapsed_s"].iloc[-1] == pytest.approx(10151, abs=2)
    units = df.attrs["units"]
    assert (
        units["step_length"],
        units["form_power"],
        units["position_lat"],
        units["elapsed_s"],
    ) == (
        "mm",
        "Watts",
        "deg",
        "s",
    )


def test_golden_physics_checks(long_run):
    """Two independent channels agree, which validates the scales of fields 39, 83 and 85."""
    df = long_run.records_df()
    moving = df[(df["step_length"] > 0) & (df["cadence"] > 0)]
    ratio = 100 * moving["vertical_oscillation"] / moving["step_length"]
    assert (moving["vertical_ratio"] - ratio).abs().median() < 0.2  # percentage points
    from_speed = 1000 * moving["speed"] / (2 * moving["cadence"] / 60)  # mm per step
    assert moving["step_length"].median() == pytest.approx(from_speed.median(), rel=0.02)


def test_golden_summary(long_run):
    s = long_run.summary()
    assert (s["device"], s["manufacturer"], s["sport"]) == ("COROS PACE Pro", "coros", "running")
    assert s["start"] == datetime(2026, 2, 28, 13, 43, 54, tzinfo=UTC)
    assert s["utc_offset"] == timedelta(hours=1)
    assert (s["distance_m"], s["timer_time_s"], s["elapsed_time_s"]) == pytest.approx(
        (30385.53, 9823.74, 10151.56)
    )
    assert (s["avg_heart_rate_bpm"], s["max_heart_rate_bpm"], s["avg_power_w"]) == (143, 162, 252)
    assert (s["records"], s["laps"], s["warnings"]) == (9825, 31, [])
    assert set(s["dev_fields"]) == {
        "form_power",
        "leg_spring_stiffness",
        "air_power",
        "impact_loading_rate",
        "effort_pace",
    }


def test_golden_dev_fields(long_run):
    by_column = {d.column: d for d in long_run.dev_fields}
    form_power = by_column["form_power"]
    assert (form_power.name, form_power.units, form_power.manufacturer) == (
        "Form Power",
        "Watts",
        "coros",
    )
    assert by_column["impact_loading_rate"].base_type == 0x85  # sint32
    assert len(long_run.dev_fields) == 5  # Effort Pace is described twice; counted once


def test_golden_interval_laps(fit_file):
    laps = sl.read(fit_file("intervals_example")).laps_df()
    assert list(laps["message_index"]) == list(range(15))
    # CLAUDE.md counts laps from 1: reps are laps 4, 6, 8, 10, 12; rests are laps 5, 7, 9, 11
    reps, rests = laps.iloc[[3, 5, 7, 9, 11]], laps.iloc[[4, 6, 8, 10]]
    assert (reps["avg_speed"] > 4.0).all() and (reps["total_distance"] == 1000).all()
    assert ((rests["total_timer_time"] - 150).abs() < 5).all()


def test_golden_treadmill_columns(fit_file):
    df = sl.read(fit_file("treadmill")).records_df()
    assert not {"position_lat", "step_length", "altitude"} & set(df.columns)
    assert {"form_power", "leg_spring_stiffness", "impact_loading_rate"} <= set(df.columns)


def test_cli_prints_the_summary(fit_file, capsys):
    main([str(fit_file("long_run"))])
    out = capsys.readouterr().out
    assert "device" in out and "COROS PACE Pro" in out
    assert "distance_m" in out and "30385.53" in out
    assert "warning" not in out


# ── synthetic ─────────────────────────────────────────────────────────────────


def records_file() -> bytes:
    fields = [(253, "uint32"), (3, "uint8"), (6, "uint16"), (73, "uint32")]
    w = FitWriter().define(0, 0, [(1, "uint16"), (8, "string", 8)]).data(0, 294, "Pod")
    w.define(1, 20, fields)
    w.data(1, fit_time(T0), 140, 3000, 3100)  # enhanced_speed wins
    w.data(1, fit_time(T0) + 1, 0xFF, 3000, 0xFFFFFFFF)  # no enhanced value: speed stays
    w.data(1, fit_time(T0) + 5, 0, 0xFFFF, 0xFFFFFFFF)  # zero stays zero, missing is NaN
    return w.to_bytes()


def test_records_df_fills_speed_from_enhanced_speed():
    df = sl.read(records_file()).records_df()
    assert df["speed"].tolist()[:2] == pytest.approx([3.1, 3.0])
    assert pd.isna(df["speed"].iloc[2])
    assert df["heart_rate"].tolist()[0] == 140 and pd.isna(df["heart_rate"].iloc[1])
    assert df["heart_rate"].iloc[2] == 0
    assert df["elapsed_s"].tolist() == [0, 1, 5]
    assert df.index[0] == T0


def test_read_accepts_bytes_and_paths(tmp_path):
    path = tmp_path / "run.fit"
    path.write_bytes(records_file())
    for source in (records_file(), path, str(path)):
        act = sl.read(source)
        assert (len(act.records), act.file_id["product_name"], act.warnings) == (3, "Pod", [])


def test_raw_mode_activity():
    act = sl.read(records_file(), raw=True)
    assert act.records[0]["field_3"] == 140 and act.dev_fields == [] and act.units == {}
    with pytest.raises(ValueError, match="raw=False"):
        act.summary()
    with pytest.raises(ValueError, match="raw=False"):
        act.records_df()


def test_warnings_are_collected_not_raised():
    data = records_file()
    act = sl.read(data[:-1] + bytes([data[-1] ^ 0xFF]))
    assert len(act.records) == 3 and len(act.warnings) == 1 and "CRC" in act.warnings[0]
