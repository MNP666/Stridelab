"""Cross-checks against Garmin's official SDK (hard rule 6).

garmin-fit-sdk is a dev dependency used only here; the package never imports it.
"""

import math
import re
from datetime import UTC, datetime

import pytest

from stridelab import iter_messages
from stridelab.profile import BASE_TYPES, ENUMS, MESSAGES
from stridelab.reader import FIT_EPOCH, SEMICIRCLES_TO_DEGREES

sdk = pytest.importorskip("garmin_fit_sdk")
from garmin_fit_sdk.fit import BASE_TYPE_DEFINITIONS


def first(value):
    return value[0] if isinstance(value, list) else value


def test_base_types_match_the_sdk():
    for num, bt in BASE_TYPES.items():
        theirs = BASE_TYPE_DEFINITIONS[num]
        assert (bt.code, bt.size) == (theirs["type_code"], theirs["size"]), bt.name
        if bt.name != "string":  # the SDK writes 0 for "empty string"
            assert bt.invalid == theirs["invalid"], bt.name


def test_profile_fields_match_the_sdk():
    for num, (name, fields) in MESSAGES.items():
        theirs = sdk.Profile["messages"][num]
        assert theirs["name"] == name
        for field_num, f in fields.items():
            p = theirs["fields"][field_num]
            where = f"{name}.{field_num}"
            assert (f.name, f.type) == (p["name"], p["type"]), where
            assert f.scale == (first(p["scale"]) or 1), where
            assert f.offset == (first(p["offset"]) or 0), where
            assert f.units == (first(p["units"]) or None), where


def test_enum_codes_match_the_sdk():
    for name, codes in ENUMS.items():
        for code, value in codes.items():
            assert sdk.Profile["types"][name][code] == value, f"{name}.{code}"


def same(ours, theirs, *, invalid=None) -> bool:
    """Equal, allowing for the places where we follow CLAUDE.md and the SDK doesn't."""
    if ours is None and invalid is not None and theirs == invalid:
        return True  # the SDK keeps raw sentinels in developer fields; we filter them (rule 3)
    if isinstance(theirs, float) and math.isnan(theirs):
        return ours is None  # the SDK keeps invalid floats as NaN; we use None
    if isinstance(ours, datetime) and isinstance(theirs, int):
        return ours == datetime.fromtimestamp(theirs + FIT_EPOCH, UTC)  # SDK: local_date_time raw
    if isinstance(ours, str) and isinstance(theirs, int):
        return any(codes.get(theirs) == ours for codes in ENUMS.values())  # SDK: some enums raw
    if isinstance(ours, bytes) and len(ours) == 1 and not isinstance(theirs, list):
        # A field whose size isn't a multiple of its base type (Coros event.data: uint32 in 1
        # byte) stays bytes for us; the SDK reads the byte as an int, or drops it as invalid.
        return theirs in (ours[0], None)
    if isinstance(ours, float) or isinstance(theirs, float):
        return None not in (ours, theirs) and math.isclose(ours, theirs, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(ours, bytes) and isinstance(theirs, list):
        return list(ours) == theirs
    return ours == theirs


@pytest.mark.parametrize("name", ["long_run", "intervals_example", "treadmill"])
def test_every_decoded_value_matches_the_sdk(fit_file, name):
    path = fit_file(name)
    theirs, errors = sdk.Decoder(sdk.Stream.from_file(str(path))).read()
    assert errors == []
    ours: dict[str, list] = {}
    for m in iter_messages(path):
        ours.setdefault(m.name, []).append(m)
    # The SDK keys developer fields by the order of the field_description messages.
    descriptions = [m.fields for m in ours["field_description"]]
    dev_names = {
        i: re.sub(r"\W+", "_", d["field_name"].lower()).strip("_")
        for i, d in enumerate(descriptions)
    }
    dev_invalid = {
        i: BASE_TYPES[d["fit_base_type_id"] & 0x1F].invalid for i, d in enumerate(descriptions)
    }

    mismatches = []
    for msg_name, messages in ours.items():
        their_messages = theirs[f"{msg_name}_mesgs"]
        assert len(messages) == len(their_messages), msg_name
        for ours_msg, their_msg in zip(messages, their_messages):
            for key, value in ours_msg.fields.items():
                expected = their_msg.get(key)
                if key in ("position_lat", "position_long") and expected is not None:
                    expected *= SEMICIRCLES_TO_DEGREES
                if not same(value, expected):
                    mismatches.append((msg_name, ours_msg.offset, key, value, expected))
            for index, expected in their_msg.get("developer_fields", {}).items():
                value = ours_msg.dev_fields.get(dev_names[index])
                if not same(value, expected, invalid=dev_invalid[index]):
                    mismatches.append(
                        (msg_name, ours_msg.offset, dev_names[index], value, expected)
                    )
    assert mismatches[:10] == []
