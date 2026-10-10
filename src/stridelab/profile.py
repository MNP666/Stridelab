"""FIT profile data, copied from the FIT SDK Profile.xlsx. Data only, no logic.

Base types only for now; messages, fields and enums arrive with profile decoding (slice 01, M2).
"""

from typing import NamedTuple


class BaseType(NamedTuple):
    name: str
    code: str  # struct format character
    size: int  # bytes per element
    invalid: int | None  # raw sentinel; floats: the all-ones bit pattern; string: None (= empty)


# Keyed by base_type & 0x1F. The high bit (0x80) only says the type is wider than one byte.
BASE_TYPES: dict[int, BaseType] = {
    0x00: BaseType("enum", "B", 1, 0xFF),
    0x01: BaseType("sint8", "b", 1, 0x7F),
    0x02: BaseType("uint8", "B", 1, 0xFF),
    0x03: BaseType("sint16", "h", 2, 0x7FFF),
    0x04: BaseType("uint16", "H", 2, 0xFFFF),
    0x05: BaseType("sint32", "i", 4, 0x7FFFFFFF),
    0x06: BaseType("uint32", "I", 4, 0xFFFFFFFF),
    0x07: BaseType("string", "s", 1, None),
    0x08: BaseType("float32", "f", 4, 0xFFFFFFFF),
    0x09: BaseType("float64", "d", 8, 0xFFFFFFFFFFFFFFFF),
    0x0A: BaseType("uint8z", "B", 1, 0),
    0x0B: BaseType("uint16z", "H", 2, 0),
    0x0C: BaseType("uint32z", "I", 4, 0),
    0x0D: BaseType("byte", "B", 1, 0xFF),  # invalid only when every byte is 0xFF
    0x0E: BaseType("sint64", "q", 8, 0x7FFFFFFFFFFFFFFF),
    0x0F: BaseType("uint64", "Q", 8, 0xFFFFFFFFFFFFFFFF),
    0x10: BaseType("uint64z", "Q", 8, 0),
}
