"""Minimal FIT encoder for synthetic reader tests.

Deliberately independent of the reader: its own type table and its own CRC (the
FIT SDK's nibble loop), so a bug shared by encoder and decoder can't hide.

    data = (
        FitWriter()
        .define(0, 20, [(253, "uint32"), (3, "uint8")], endian=">")
        .data(0, 1_000_000_000, 150)
        .to_bytes()
    )
"""

import struct

# type name -> (base type byte, struct code)
BASE = {
    "enum": (0x00, "B"),
    "sint8": (0x01, "b"),
    "uint8": (0x02, "B"),
    "sint16": (0x83, "h"),
    "uint16": (0x84, "H"),
    "sint32": (0x85, "i"),
    "uint32": (0x86, "I"),
    "string": (0x07, "s"),
    "float32": (0x88, "f"),
    "float64": (0x89, "d"),
    "uint8z": (0x0A, "B"),
    "uint16z": (0x8B, "H"),
    "uint32z": (0x8C, "I"),
    "byte": (0x0D, "B"),
    "sint64": (0x8E, "q"),
    "uint64": (0x8F, "Q"),
    "uint64z": (0x90, "Q"),
}

_NIBBLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip


def crc16(data: bytes) -> int:
    """The FIT SDK's CRC algorithm, nibble by nibble."""
    crc = 0
    for byte in data:
        for nibble in (byte & 0x0F, byte >> 4):
            tmp = _NIBBLE[crc & 0x0F]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ _NIBBLE[nibble]
    return crc


class FitWriter:
    def __init__(self):
        self._data = bytearray()
        self._defs: dict[int, tuple[str, list[tuple[int, str | int, int]]]] = {}

    def define(
        self,
        local: int,
        global_num: int,
        fields: list[tuple],
        *,
        dev_fields: list[tuple[int, int, int]] = (),
        endian: str = "<",
    ):
        """fields: (field_num, type) or (field_num, type, size). type is a name from BASE,
        or a raw base-type byte (then values must be bytes).
        dev_fields: (field_num, size, developer_data_index); their values are bytes."""
        full = [(f[0], f[1], f[2] if len(f) > 2 else _size(f[1])) for f in fields]
        header = 0x40 | local | (0x20 if dev_fields else 0)
        self._data += bytes([header, 0, 0 if endian == "<" else 1])
        self._data += struct.pack(endian + "H", global_num) + bytes([len(full)])
        for num, typ, size in full:
            self._data += bytes([num, size, BASE[typ][0] if isinstance(typ, str) else typ])
        if dev_fields:
            self._data += bytes([len(dev_fields)])
            for num, size, dev_index in dev_fields:
                self._data += bytes([num, size, dev_index])
        dev = [(num, None, size) for num, size, _ in dev_fields]
        self._defs[local] = (endian, full + dev)
        return self

    def data(self, local: int, *values):
        """Values in definition order, dev fields last: int/float, tuple for arrays,
        str for strings, bytes as-is."""
        endian, fields = self._defs[local]
        self._data.append(local)
        for (_, typ, size), value in zip(fields, values, strict=True):
            self._data += _encode(endian, typ, size, value)
        return self

    def append(self, raw: bytes):
        """Append bytes verbatim, for malformed input."""
        self._data += raw
        return self

    def to_bytes(
        self, *, header_size: int = 14, header_crc: bool | int = True, data_size: int | None = None
    ) -> bytes:
        """header_crc: True computes it, False writes 0 (unset), an int is written as-is."""
        size = len(self._data) if data_size is None else data_size
        header = struct.pack("<BBHI4s", header_size, 0x20, 2132, size, b".FIT")
        if header_size == 14:
            value = crc16(header) if header_crc is True else int(header_crc)
            header += struct.pack("<H", value)
        body = header + bytes(self._data)
        return body + struct.pack("<H", crc16(body))


def _size(typ: str) -> int:
    return 1 if typ == "string" else struct.calcsize("<" + BASE[typ][1])


def _encode(endian: str, typ: str | int, size: int, value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        assert len(value) == size, f"{len(value)} bytes for a {size}-byte field"
        return bytes(value)
    code = BASE[typ][1]
    if typ == "string":
        return value.encode().ljust(size, b"\0")[:size]
    if isinstance(value, tuple):
        return struct.pack(f"{endian}{len(value)}{code}", *value)
    return struct.pack(endian + code, value)
