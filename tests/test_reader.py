"""Reader tests: golden files (skipped if data/raw/ is empty) and synthetic files from fitwriter."""

from collections import Counter

import pytest
from fitwriter import FitWriter
from fitwriter import crc16 as sdk_crc16

from stridelab import FitParseError, iter_messages
from stridelab.reader import crc16


def read_raw(source, **kwargs):
    return list(iter_messages(source, raw=True, **kwargs))


# ── golden files ──────────────────────────────────────────────────────────────

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


GOLDEN_DEV_KEYS = {  # message number -> raw dev field keys, named in M3
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
    [file_id] = [m for m in msgs if m.num == 0]
    [session] = [m for m in msgs if m.num == 18]
    assert file_id.fields["field_1"] == 294  # manufacturer: coros
    assert session.fields["field_9"] == 3038553  # total_distance, scale 100 -> 30385.53 m
    assert session.fields["field_16"] == 143  # avg_heart_rate
    assert session.fields["field_17"] == 162  # max_heart_rate


# ── synthetic: values ─────────────────────────────────────────────────────────

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
def test_dev_field_bytes_are_kept_until_they_can_be_named(endian):
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
    assert (
        len(read_raw(one_record().data(0, 1).to_bytes(header_crc=0xBEEF), warnings=warnings)) == 1
    )
    assert len(warnings) == 1 and "header CRC mismatch" in warnings[0]


# ── synthetic: structural problems raise with the offset ──────────────────────


def patched(data: bytes, at: int, new: bytes) -> bytes:
    return data[:at] + new + data[at + len(new) :]


GOOD = one_record().data(0, 1).to_bytes()
STRUCTURAL_ERRORS = [
    pytest.param(patched(GOOD, 0, b"\x0d"), 0, "header size", id="header-size"),
    pytest.param(patched(GOOD, 8, b"XFIT"), 8, "signature", id="signature"),
    pytest.param(GOOD[:-3], len(GOOD) - 3, "truncated", id="truncated"),
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
        one_record().append(b"\x80\x01").to_bytes(), 23, "compressed timestamp", id="compressed"
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


def test_profile_decoding_is_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        iter_messages(b"")
