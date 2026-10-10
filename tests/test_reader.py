"""Reader tests: golden files (skipped if data/raw/ is empty) and synthetic files from fitwriter."""

import struct
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fitwriter import FitWriter
from fitwriter import crc16 as sdk_crc16

from stridelab import FitParseError, iter_messages
from stridelab.reader import FIT_EPOCH, crc16


def read_raw(source, **kwargs):
    return list(iter_messages(source, raw=True, **kwargs))


def read(source):
    """Profile-decoded messages, asserting there were no warnings."""
    warnings = []
    msgs = list(iter_messages(source, warnings=warnings))
    assert warnings == []
    return msgs


def only(msgs, name):
    [m] = [m for m in msgs if m.name == name]
    return m


def fit_time(dt: datetime) -> int:
    return int(dt.timestamp()) - FIT_EPOCH


T0 = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)


# ── golden files: raw walk ────────────────────────────────────────────────────

GOLDEN_COUNTS = {  # record, lap, event, session
    "long_run": {20: 9825, 19: 31, 21: 10, 18: 1},
    "intervals_example": {20: 3237, 19: 15, 21: 6, 18: 1},
    "treadmill": {20: 3558, 19: 18, 21: 4, 18: 1},
}
STANDARD_MESSAGES = {0, 18, 19, 20, 21, 23, 34, 206, 207}


@pytest.mark.parametrize("name", GOLDEN_COUNTS)
def test_golden_walk_reaches_the_end(fit_file, name):
    warnings = []
    counts = Counter(m.num for m in read_raw(fit_file(name), warnings=warnings))
    assert warnings == []  # header and file CRC valid
    assert {num: counts[num] for num in GOLDEN_COUNTS[name]} == GOLDEN_COUNTS[name]
    assert set(counts) <= STANDARD_MESSAGES


GOLDEN_DEV_KEYS = {  # message number -> raw dev field keys
    "long_run": {
        20: {"dev_0_8", "dev_0_9", "dev_0_11", "dev_0_16", "dev_0_24"},
        19: {"dev_0_16"},
        18: {"dev_0_16"},
    },
    "intervals_example": {20: {"dev_0_16"}, 19: {"dev_0_16"}, 18: {"dev_0_16"}},
    "treadmill": {20: {"dev_0_8", "dev_0_9", "dev_0_24"}},
}


@pytest.mark.parametrize("name", GOLDEN_DEV_KEYS)
def test_golden_dev_field_layout(fit_file, name):
    keys: dict[int, set[str]] = {}
    for m in read_raw(fit_file(name)):
        if m.dev_fields:
            keys.setdefault(m.num, set()).update(m.dev_fields)
    assert keys == GOLDEN_DEV_KEYS[name]


def test_golden_raw_values(fit_file):
    msgs = read_raw(fit_file("long_run"))
    file_id, session = only(msgs, "mesg_0"), only(msgs, "mesg_18")
    assert file_id.fields["field_1"] == 294  # manufacturer: coros
    assert session.fields["field_9"] == 3038553  # total_distance, scale 100 -> 30385.53 m
    assert session.fields["field_16"] == 143  # avg_heart_rate
    assert session.fields["field_17"] == 162  # max_heart_rate


# ── golden files: profile decoding and developer fields ───────────────────────


def test_golden_session(fit_file):
    s = only(read(fit_file("long_run")), "session").fields
    assert s["total_distance"] == pytest.approx(30385.53)
    assert s["total_timer_time"] == pytest.approx(9823.74)
    assert s["total_elapsed_time"] == pytest.approx(10151.56)
    assert (s["avg_heart_rate"], s["max_heart_rate"]) == (143, 162)
    assert (s["avg_speed"], s["max_speed"]) == pytest.approx((3.093, 3.846))
    assert (s["total_calories"], s["total_ascent"], s["total_descent"]) == (2330, 218, 226)
    assert (s["avg_power"], s["avg_cadence"], s["sport"]) == (252, 91, "running")
    # field 91: the Rust version called this a Coros "training load"; it is the stance time
    assert s["avg_stance_time"] == pytest.approx(236.0)


def test_golden_metadata_and_time(fit_file):
    msgs = read(fit_file("long_run"))
    file_id, activity = only(msgs, "file_id").fields, only(msgs, "activity").fields
    assert (file_id["type"], file_id["manufacturer"]) == ("activity", "coros")
    assert (
        file_id["product_name"]
        == only(msgs, "device_info").fields["product_name"]
        == "COROS PACE Pro"
    )
    assert activity["local_timestamp"] - activity["timestamp"] == timedelta(hours=1)
    records = [m.fields for m in msgs if m.name == "record"]
    assert all(r["timestamp"].tzinfo is UTC for r in records)
    assert records[0]["timestamp"] >= only(msgs, "session").fields["start_time"]
    timer = [(m.fields["event"], m.fields["event_type"]) for m in msgs if m.name == "event"]
    assert timer == [("timer", "start"), ("timer", "stop_all")] * 5


def test_golden_lap_and_session_use_different_field_numbers(fit_file):
    msgs = read_raw(fit_file("long_run"))
    lap, session = next(m for m in msgs if m.num == 19), only(msgs, "mesg_18")
    assert "field_13" in lap.fields and "field_14" in session.fields  # both are avg_speed
    decoded = read(fit_file("long_run"))
    assert next(m for m in decoded if m.name == "lap").fields["avg_speed"] == pytest.approx(3.103)


def test_golden_treadmill(fit_file):
    msgs = read(fit_file("treadmill"))
    assert only(msgs, "session").fields["sub_sport"] == "indoor_running"
    keys = set().union(*(m.fields.keys() for m in msgs if m.name == "record"))
    assert not keys & {"position_lat", "position_long", "step_length", "altitude"}


GOLDEN_DEV_NAMES = {  # message name -> dev field columns
    "long_run": {
        "record": {
            "form_power",
            "leg_spring_stiffness",
            "air_power",
            "impact_loading_rate",
            "effort_pace",
        },
        "lap": {"effort_pace"},
        "session": {"effort_pace"},
    },
    "intervals_example": {
        "record": {"effort_pace"},
        "lap": {"effort_pace"},
        "session": {"effort_pace"},
    },
    "treadmill": {"record": {"form_power", "leg_spring_stiffness", "impact_loading_rate"}},
}


@pytest.mark.parametrize("name", GOLDEN_DEV_NAMES)
def test_golden_dev_fields_by_name(fit_file, name):
    keys: dict[str, set[str]] = {}
    for m in read(fit_file(name)):
        if m.dev_fields:
            keys.setdefault(m.name, set()).update(m.dev_fields)
    assert keys == GOLDEN_DEV_NAMES[name]


def test_golden_dev_values(fit_file):
    record = [m for m in read(fit_file("long_run")) if m.name == "record"][100].dev_fields
    assert isinstance(record["impact_loading_rate"], int)  # sint32
    assert isinstance(record["effort_pace"], float)  # float32, m/s
    assert 50 < record["form_power"] < 150  # watts; a sanity range, not how the field was named


# ── synthetic: raw values ─────────────────────────────────────────────────────

RECORD_FIELDS = [
    (253, "uint32"),
    (0, "sint32"),
    (3, "uint8"),
    (6, "uint16"),
    (7, "float32"),
    (8, "uint16", 6),  # array of 3
    (9, "string", 8),
    (10, "byte", 4),
    (11, "uint16", 3),  # not a multiple of the base size -> raw bytes
]
RECORD_VALUES = (
    1_000_000_000,
    -123_456,
    150,
    3500,
    1.5,
    (1, 2, 3),
    "Coros",
    b"\x01\x02\x03\x04",
    b"\xaa\xbb\xcc",
)


def one_record() -> FitWriter:
    # 14-byte header + 9-byte definition (6 + 3 per field): the first data message is at byte 23
    return FitWriter().define(0, 20, [(3, "uint8")])


def test_crc16_is_crc16_arc_and_matches_the_sdk_algorithm():
    assert crc16(b"123456789") == 0xBB3D  # CRC-16/ARC check value
    data = bytes(range(256)) * 3
    assert crc16(data) == sdk_crc16(data)


@pytest.mark.parametrize("endian", ["<", ">"])
def test_roundtrip_both_endians(endian):
    data = (
        FitWriter().define(0, 20, RECORD_FIELDS, endian=endian).data(0, *RECORD_VALUES).to_bytes()
    )
    [msg] = read_raw(data)
    assert (msg.num, msg.name, msg.dev_fields) == (20, "mesg_20", {})
    assert msg.fields == {f"field_{f[0]}": v for f, v in zip(RECORD_FIELDS, RECORD_VALUES)}


def test_raw_mode_keeps_sentinels():
    data = (
        FitWriter()
        .define(0, 20, [(3, "uint8"), (6, "uint16"), (9, "string", 4)])
        .data(0, 0xFF, 0xFFFF, "")
        .to_bytes()
    )
    assert read_raw(data)[0].fields == {"field_3": 0xFF, "field_6": 0xFFFF, "field_9": ""}


def test_offsets_and_redefinition_of_a_local_type():
    data = one_record().data(0, 1).define(0, 21, [(0, "enum")]).data(0, 2).to_bytes()
    msgs = read_raw(data)
    assert [(m.num, m.fields) for m in msgs] == [(20, {"field_3": 1}), (21, {"field_0": 2})]
    assert [m.offset for m in msgs] == [23, 23 + 2 + 9]


def test_chained_files_and_12_byte_header():
    first = one_record().data(0, 1).to_bytes(header_crc=False)
    second = one_record().data(0, 2).to_bytes(header_size=12)
    assert [m.fields["field_3"] for m in read_raw(first + second)] == [1, 2]


@pytest.mark.parametrize("endian", ["<", ">"])
def test_dev_field_bytes_in_raw_mode(endian):
    data = (
        FitWriter()
        .define(0, 20, [(3, "uint8")], dev_fields=[(0, 2, 0), (5, 4, 1)], endian=endian)
        .data(0, 150, b"\x01\x02", b"\x03\x04\x05\x06")
        .to_bytes()
    )
    [msg] = read_raw(data)
    assert msg.fields == {"field_3": 150}
    assert msg.dev_fields == {"dev_0_0": b"\x01\x02", "dev_1_5": b"\x03\x04\x05\x06"}


def test_unknown_base_type_is_kept_as_bytes_with_a_warning():
    data = FitWriter().define(0, 20, [(3, 0x1F, 2)]).data(0, b"\x01\x02").to_bytes()
    warnings = []
    assert read_raw(data, warnings=warnings)[0].fields == {"field_3": b"\x01\x02"}
    assert len(warnings) == 1 and "unknown base type" in warnings[0]


# ── synthetic: profile decoding ───────────────────────────────────────────────


def test_names_scale_offset_time_enum_and_degrees():
    fields = [
        (253, "uint32"),
        (0, "sint32"),
        (2, "uint16"),
        (3, "uint8"),
        (6, "uint16"),
        (42, "enum"),
    ]
    data = (
        FitWriter()
        .define(0, 20, fields)
        .data(0, fit_time(T0), 2**30, (100 + 500) * 5, 150, 3500, 1)
        .to_bytes()
    )
    assert read(data)[0].fields == {
        "timestamp": T0,
        "position_lat": 90.0,  # 2**30 semicircles
        "altitude": 100.0,  # raw / 5 - 500
        "heart_rate": 150,  # unscaled values stay int
        "speed": 3.5,
        "activity_type": "running",
    }


def test_sentinels_become_none_and_zero_stays_zero():
    fields = [(3, "uint8"), (4, "uint8"), (6, "uint16"), (2, "uint16"), (39, "uint16")]
    data = FitWriter().define(0, 20, fields).data(0, 0xFF, 0, 0xFFFF, 0xFFFF, 0).to_bytes()
    f = read(data)[0].fields
    assert (f["heart_rate"], f["cadence"], f["speed"]) == (None, 0, None)
    assert f["altitude"] is None  # sentinel checked on the raw value, before the offset
    assert f["vertical_oscillation"] == 0.0


def test_field_shapes_and_their_sentinels():
    fields = [
        (200, "uint16z"),
        (201, "uint16z"),
        (202, "float32"),
        (203, "uint16", 6),  # array, one element invalid
        (204, "uint16", 4),  # array, all invalid
        (205, "string", 6),
        (206, "byte", 3),
        (207, "uint16", 3),  # odd size: bytes, no sentinel to check
    ]
    nan = struct.unpack("<f", b"\xff\xff\xff\xff")[0]
    values = (0, 7, nan, (1, 0xFFFF, 3), (0xFFFF, 0xFFFF), "", b"\xff\xff\xff", b"\xff\xff\xff")
    f = read(FitWriter().define(0, 20, fields).data(0, *values).to_bytes())[0].fields
    assert f == {
        "field_200": None,  # uint16z: 0 is invalid
        "field_201": 7,
        "field_202": None,
        "field_203": (1, None, 3),
        "field_204": None,
        "field_205": None,
        "field_206": None,
        "field_207": b"\xff\xff\xff",
    }


def test_unknown_message_field_and_enum_value():
    data = (
        FitWriter()
        .define(0, 300, [(1, "uint8")])
        .data(0, 5)
        .define(1, 18, [(5, "enum"), (99, "uint16")])
        .data(1, 77, 1234)
        .to_bytes()
    )
    unknown, session = read(data)
    assert (unknown.name, unknown.fields) == ("mesg_300", {"field_1": 5})
    assert session.fields == {"sport": "sport_77", "field_99": 1234}


def test_vendor_fields_apply_only_to_their_manufacturer(monkeypatch):
    from stridelab import profile

    monkeypatch.setitem(
        profile.VENDOR_FIELDS, ("stryd", 18), {99: profile.Field("vendor_thing", "uint16", 10)}
    )

    def file_from(manufacturer: int) -> bytes:
        return (
            FitWriter()
            .define(0, 0, [(1, "uint16")])
            .data(0, manufacturer)
            .define(1, 18, [(99, "uint16")])
            .data(1, 1234)
            .to_bytes()
        )

    assert read(file_from(95))[1].fields == {"vendor_thing": 123.4}  # 95 = stryd
    assert read(file_from(294))[1].fields == {"field_99": 1234}  # coros


# ── synthetic: developer fields ───────────────────────────────────────────────


def describe(
    w: FitWriter, num: int, base_type: int, name: str, units: str, scale=0xFF, offset=0x7F
):
    """Append developer_data_id 0 (stryd) and a field_description for dev field `num`."""
    fields = [
        (0, "uint8"),
        (1, "uint8"),
        (2, "uint8"),
        (3, "string", 16),
        (6, "uint8"),
        (7, "sint8"),
        (8, "string", 8),
    ]
    return (
        w.define(2, 207, [(2, "uint16"), (3, "uint8")])
        .data(2, 95, 0)
        .define(3, 206, fields)
        .data(3, 0, num, base_type, name, scale, offset, units)
    )


@pytest.mark.parametrize("endian", ["<", ">"])
def test_dev_field_is_named_typed_and_scaled(endian):
    w = describe(FitWriter(), 8, 0x84, "Form Power", "Watts", scale=10, offset=5)
    w.define(0, 20, [(3, "uint8")], dev_fields=[(8, 2, 0)], endian=endian)
    w.data(0, 150, struct.pack(endian + "H", 2550)).data(0, 151, struct.pack(endian + "H", 0xFFFF))
    first, second = [m for m in read(w.to_bytes()) if m.name == "record"]
    assert first.dev_fields == {"form_power": 250.0}  # 2550 / 10 - 5
    assert second.dev_fields == {"form_power": None}


def test_dev_field_name_clash_gets_dev_prefix():
    w = describe(FitWriter(), 1, 0x84, "Heart Rate", "bpm")
    w.define(0, 20, [(3, "uint8")], dev_fields=[(1, 2, 0)]).data(0, 150, struct.pack("<H", 151))
    record = next(m for m in read(w.to_bytes()) if m.name == "record")
    assert (record.fields, record.dev_fields) == ({"heart_rate": 150}, {"dev_heart_rate": 151})


def test_dev_field_without_description_stays_bytes_with_a_warning():
    data = (
        FitWriter()
        .define(0, 20, [(3, "uint8")], dev_fields=[(4, 2, 0)])
        .data(0, 1, b"\x01\x02")
        .to_bytes()
    )
    warnings = []
    [msg] = iter_messages(data, warnings=warnings)
    assert msg.dev_fields == {"dev_0_4": b"\x01\x02"}
    assert len(warnings) == 1 and "no field_description" in warnings[0]


# ── synthetic: compressed timestamps ──────────────────────────────────────────


def test_compressed_timestamps():
    t = fit_time(T0)  # its low 5 bits are the reference for the 5-bit offsets
    w = FitWriter().define(0, 20, [(253, "uint32"), (3, "uint8")]).define(1, 20, [(3, "uint8")])
    w.data(0, t, 140)
    w.compressed(1, (t + 2) & 0x1F, 141)  # +2 s
    w.compressed(1, (t + 2 + 31) & 0x1F, 142)  # +31 s: rolls over the 5-bit offset
    w.define(2, 21, [(253, "uint32"), (0, "enum")]).data(2, t + 100, 0)  # any message's 253 counts
    w.compressed(1, (t + 101) & 0x1F, 143)
    msgs = [m for m in read(w.to_bytes()) if m.name == "record"]
    seconds = [(m.fields["timestamp"] - T0).total_seconds() for m in msgs]
    assert seconds == [0, 2, 33, 101]
    assert [m.fields["heart_rate"] for m in msgs] == [140, 141, 142, 143]
    assert read_raw(w.to_bytes())[1].fields == {"field_253": t + 2, "field_3": 141}


# ── synthetic: recoverable problems are warnings ──────────────────────────────


def test_file_crc_mismatch_is_a_warning():
    good = one_record().data(0, 1).to_bytes()
    bad = good[:-1] + bytes([good[-1] ^ 0xFF])
    warnings = []
    assert len(read_raw(bad, warnings=warnings)) == 1
    assert len(warnings) == 1 and "file CRC mismatch" in warnings[0]
    with pytest.warns(UserWarning, match="file CRC mismatch"):  # no list given: warnings.warn
        read_raw(bad)


def test_header_crc_mismatch_is_a_warning():
    warnings = []
    data = one_record().data(0, 1).to_bytes(header_crc=0xBEEF)
    assert len(read_raw(data, warnings=warnings)) == 1
    assert len(warnings) == 1 and "header CRC mismatch" in warnings[0]


def test_truncated_file_keeps_complete_messages_with_warnings():
    full = one_record().data(0, 1).data(0, 2).data(0, 3).to_bytes()
    warnings = []
    msgs = read_raw(full[:-3], warnings=warnings)  # CRC and half of the last message gone
    assert [m.fields["field_3"] for m in msgs] == [1, 2]
    assert len(warnings) == 2
    assert "file truncated" in warnings[0] and "incomplete message at byte 27" in warnings[1]


def test_missing_crc_only_is_a_warning():
    warnings = []
    assert len(read_raw(one_record().data(0, 1).to_bytes()[:-2], warnings=warnings)) == 1
    assert len(warnings) == 1 and "CRC not checked" in warnings[0]


# ── synthetic: structural problems raise with the offset ──────────────────────


def patched(data: bytes, at: int, new: bytes) -> bytes:
    return data[:at] + new + data[at + len(new) :]


GOOD = one_record().data(0, 1).to_bytes()
STRUCTURAL_ERRORS = [
    pytest.param(patched(GOOD, 0, b"\x0d"), 0, "header size", id="header-size"),
    pytest.param(patched(GOOD, 8, b"XFIT"), 8, "signature", id="signature"),
    pytest.param(GOOD[:10], 0, "header truncated", id="header-truncated"),
    pytest.param(
        one_record().data(0, 1).to_bytes(data_size=10), 23, "runs past end", id="past-end"
    ),
    pytest.param(
        one_record().append(b"\x01\x05").to_bytes(),
        23,
        "undefined local type 1",
        id="undefined-local",
    ),
    pytest.param(
        FitWriter().append(bytes([0x40, 0, 2, 20, 0, 0])).to_bytes(), 16, "architecture", id="arch"
    ),
    pytest.param(
        one_record().append(b"\x80\x01").to_bytes(),
        23,
        "compressed timestamp before",
        id="no-timestamp-yet",
    ),
    pytest.param(
        FitWriter().append(bytes([0x60, 0, 0, 20, 0, 0, 2])).to_bytes(),
        14,
        "runs past end",
        id="dev-def-past-end",
    ),
]


@pytest.mark.parametrize("data, offset, match", STRUCTURAL_ERRORS)
def test_structural_errors_raise_with_offset(data, offset, match):
    with pytest.raises(FitParseError, match=match) as exc:
        read_raw(data, warnings=[])
    assert exc.value.offset == offset
    assert f"at byte {offset}" in str(exc.value)
