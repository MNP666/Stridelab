"""L1: bytes -> Message stream.

One generic decoder and one forward pass. Records, laps, session and scan are all
views over the messages this yields; never write a second byte walker.
"""

import struct
import warnings as pywarnings
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from .profile import BASE_TYPES

FIT_SIGNATURE = b".FIT"
FIT_STRING, FIT_BYTE = 0x07, 0x0D

# How a field's slice of the unpacked struct tuple becomes its value.
_SCALAR, _ARRAY, _TEXT, _BYTES = range(4)


class FitParseError(Exception):
    """A structural problem: the rest of the file cannot be decoded reliably."""

    def __init__(self, msg: str, offset: int):
        super().__init__(f"{msg} at byte {offset}")
        self.msg = msg
        self.offset = offset


@dataclass(slots=True)
class Message:
    num: int  # global message number
    name: str
    fields: dict[str, Any]
    dev_fields: dict[str, Any]
    offset: int  # byte offset of the record header in the source


@dataclass(slots=True)
class _Definition:
    num: int
    layout: tuple[tuple[str, int, int], ...]  # (key, kind, number of struct items)
    dev_layout: tuple[tuple[str, int, int], ...]  # dev fields come after the standard ones
    n_items: int  # struct items used by the standard fields
    struct: struct.Struct


def _crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _crc_table()


def crc16(data: bytes, crc: int = 0) -> int:
    """FIT's CRC. It is CRC-16/ARC; this byte table is ~4x faster than the SDK's nibble loop."""
    table = _CRC_TABLE
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc


def iter_messages(
    source: str | PathLike | bytes,
    *,
    raw: bool = False,
    warnings: list[str] | None = None,
) -> Iterator[Message]:
    """Yield every data message in file order.

    raw=True: keys are field_<n>, values are raw ints with sentinels kept.
    Recoverable problems (CRC mismatch, ...) are appended to `warnings` if given,
    otherwise issued with warnings.warn. Structural problems raise FitParseError.
    """
    if not raw:
        raise NotImplementedError("profile decoding arrives in slice 01 M2; pass raw=True")
    if isinstance(source, (bytes, bytearray, memoryview)):
        buf = bytes(source)
    else:
        buf = Path(source).read_bytes()
    warn = warnings.append if warnings is not None else pywarnings.warn
    return _walk(buf, warn)


def _walk(buf: bytes, warn: Callable[[str], Any]) -> Iterator[Message]:
    pos = 0
    while pos < len(buf):  # FIT files can be chained back to back
        pos = yield from _walk_file(buf, pos, warn)


def _walk_file(buf: bytes, start: int, warn: Callable[[str], Any]) -> Generator[Message, None, int]:
    data_start, data_end = _read_header(buf, start, warn)
    stored = struct.unpack_from("<H", buf, data_end)[0]
    computed = crc16(buf[start:data_end])
    if computed != stored:
        warn(
            f"file CRC mismatch: stored {stored:#06x}, computed {computed:#06x} at byte {data_end}"
        )

    defs: dict[int, _Definition] = {}
    pos = data_start
    while pos < data_end:
        header = buf[pos]
        if header & 0x80:
            raise FitParseError("compressed timestamp header not supported yet", pos)
        local = header & 0x0F
        if header & 0x40:
            defs[local], pos = _read_definition(buf, pos, data_end, warn)
            continue
        d = defs.get(local)
        if d is None:
            raise FitParseError(f"data message for undefined local type {local}", pos)
        end = pos + 1 + d.struct.size
        if end > data_end:
            raise FitParseError("data message runs past end of data", pos)
        values = d.struct.unpack_from(buf, pos + 1)
        fields = _fields(d.layout, values)
        dev_fields = _fields(d.dev_layout, values, d.n_items) if d.dev_layout else {}
        yield Message(d.num, f"mesg_{d.num}", fields, dev_fields, pos)
        pos = end
    return data_end + 2


def _read_header(buf: bytes, start: int, warn: Callable[[str], Any]) -> tuple[int, int]:
    """Validate the file header at `start`; return the data range [data_start, data_end)."""
    size = buf[start]
    if size not in (12, 14):
        raise FitParseError(f"unsupported file header size {size}", start)
    if len(buf) - start < size:
        raise FitParseError("file header truncated", start)
    if buf[start + 8 : start + 12] != FIT_SIGNATURE:
        raise FitParseError("missing .FIT signature", start + 8)
    if size == 14:
        header_crc = struct.unpack_from("<H", buf, start + 12)[0]
        if header_crc and header_crc != crc16(buf[start : start + 12]):
            warn(f"file header CRC mismatch at byte {start + 12}")
    data_size = struct.unpack_from("<I", buf, start + 4)[0]
    data_start = start + size
    data_end = data_start + data_size
    if data_end + 2 > len(buf):
        # Slice 01 M6: warn and keep what can be decoded instead of raising.
        raise FitParseError(
            f"file truncated: header promises {data_size} data bytes + CRC, "
            f"{len(buf) - data_start} bytes present",
            len(buf),
        )
    return data_start, data_end


def _read_definition(
    buf: bytes, pos: int, data_end: int, warn: Callable[[str], Any]
) -> tuple[_Definition, int]:
    """Compile the definition message at `pos` into one struct.Struct; return it and the next position."""
    if pos + 6 > data_end:
        raise FitParseError("definition message runs past end of data", pos)
    arch = buf[pos + 2]
    if arch not in (0, 1):
        raise FitParseError(f"unknown architecture byte {arch}", pos + 2)
    endian = "<" if arch == 0 else ">"  # applies to every multi-byte value, the global number too
    num = struct.unpack_from(endian + "H", buf, pos + 3)[0]
    end = pos + 6 + 3 * buf[pos + 5]
    if end > data_end:
        raise FitParseError("definition message runs past end of data", pos)

    codes, layout = [endian], []
    for i in range(pos + 6, end, 3):
        field_num, size, base_type = buf[i], buf[i + 1], buf[i + 2]
        bt = BASE_TYPES.get(base_type & 0x1F)
        if bt is None:
            warn(
                f"unknown base type {base_type:#04x} for field {field_num} of message {num}, kept as bytes, at byte {i}"
            )
            code, kind, n = f"{size}s", _BYTES, 1
        elif base_type & 0x1F == FIT_STRING:
            code, kind, n = f"{size}s", _TEXT, 1
        elif base_type & 0x1F == FIT_BYTE or size % bt.size:
            code, kind, n = f"{size}s", _BYTES, 1
        elif size == bt.size:
            code, kind, n = bt.code, _SCALAR, 1
        else:
            n = size // bt.size
            code, kind = f"{n}{bt.code}", _ARRAY
        codes.append(code)
        layout.append((f"field_{field_num}", kind, n))

    # Developer fields: (field_num, size, developer_data_index). Their types live in
    # field_description messages; until those are applied (slice 01 M3) keep raw bytes.
    dev_layout = []
    if buf[pos] & 0x20:
        if end + 1 > data_end:
            raise FitParseError("definition message runs past end of data", pos)
        dev_start, end = end + 1, end + 1 + 3 * buf[end]
        if end > data_end:
            raise FitParseError("definition message runs past end of data", pos)
        for i in range(dev_start, end, 3):
            field_num, size, dev_index = buf[i], buf[i + 1], buf[i + 2]
            codes.append(f"{size}s")
            dev_layout.append((f"dev_{dev_index}_{field_num}", _BYTES, 1))

    n_items = sum(n for _, _, n in layout)
    definition = _Definition(
        num, tuple(layout), tuple(dev_layout), n_items, struct.Struct("".join(codes))
    )
    return definition, end


def _fields(
    layout: tuple[tuple[str, int, int], ...], values: tuple, start: int = 0
) -> dict[str, Any]:
    fields = {}
    i = start
    for key, kind, n in layout:
        if kind == _ARRAY:
            fields[key] = values[i : i + n]
        elif kind == _TEXT:
            fields[key] = _text(values[i])
        else:
            fields[key] = values[i]
        i += n
    return fields


def _text(raw: bytes) -> str | bytes:
    """UTF-8 up to the first NUL. Undecodable bytes are kept as bytes rather than guessed."""
    raw = raw.split(b"\0", 1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
