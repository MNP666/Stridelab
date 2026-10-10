"""L1: bytes -> Message stream.

One generic decoder and one forward pass. Records, laps, session and scan are all
views over the messages this yields; never write a second byte walker.

Each definition message is compiled once: a struct.Struct for the bytes, plus one
converter per field (sentinel -> None, scale/offset, enum name, datetime).
"""

import math
import re
import struct
import warnings as pywarnings
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any

from .profile import BASE_TYPES, ENUMS, MESSAGES, VENDOR_FIELDS, Field

FIT_SIGNATURE = b".FIT"
FIT_EPOCH = 631_065_600  # 1989-12-31T00:00:00Z as a Unix timestamp
FIT_STRING, FIT_BYTE = 0x07, 0x0D
FIT_FLOATS = {0x08, 0x09}
TIMESTAMP = 253  # field 253 is the timestamp in every message
FILE_ID, FIELD_DESCRIPTION, DEVELOPER_DATA_ID = 0, 206, 207
SEMICIRCLES_TO_DEGREES = 180 / 2**31

# How a field's slice of the unpacked struct tuple becomes its value.
_SCALAR, _ARRAY, _TEXT, _BYTES = range(4)

Warn = Callable[[str], Any]
Convert = Callable[[Any], Any]


class FitParseError(Exception):
    """A structural problem: the rest of the file cannot be decoded reliably."""

    def __init__(self, msg: str, offset: int):
        super().__init__(f"{msg} at byte {offset}")
        self.msg = msg
        self.offset = offset


class _PastEnd(FitParseError):
    """A message runs past the end of the data: structural, unless the file is truncated."""


@dataclass(slots=True)
class Message:
    num: int  # global message number
    name: str
    fields: dict[str, Any]
    dev_fields: dict[str, Any]
    offset: int  # byte offset of the record header in the source


@dataclass(frozen=True, slots=True)
class DevField:
    key: tuple[int, int]  # (developer_data_index, field_definition_number)
    name: str  # field_name as the app wrote it, e.g. "Form Power"
    column: str  # snake_case key in messages, e.g. "form_power" (dev_ prefix on a name clash)
    units: str | None
    base_type: int
    scale: float | None
    offset: float | None
    manufacturer: str | None  # from developer_data_id


@dataclass(slots=True)
class _Definition:
    num: int
    name: str
    slots: tuple[tuple[str, int, int, Convert | None], ...]  # (key, kind, struct items, convert)
    dev_slots: tuple[tuple[str, int, int, Convert | None], ...]  # dev fields follow standard ones
    n_items: int  # struct items used by the standard fields
    struct: struct.Struct
    timestamp_item: int | None  # struct item holding field 253, for compressed timestamps


@dataclass(slots=True)
class _Context:
    """Per-file state that changes how later definitions are compiled."""

    raw: bool
    warn: Warn
    manufacturer: str | None = None  # file_id.manufacturer, for VENDOR_FIELDS
    dev_manufacturers: dict[int, str | None] = field(default_factory=dict)
    dev: dict[tuple[int, int], DevField] = field(default_factory=dict)


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


def profile_fields(num: int, manufacturer: str | None) -> tuple[str, dict[int, Field]]:
    """Message name and fields for `num`, with vendor fields when the manufacturer matches."""
    name, fields = MESSAGES.get(num, (f"mesg_{num}", {}))
    vendor = VENDOR_FIELDS.get((manufacturer, num))
    return name, {**vendor, **fields} if vendor else fields


def output_units(f: Field) -> str | None:
    """Units of the decoded value, which differ from the profile where the reader converts."""
    if f.type in ("date_time", "local_date_time"):
        return None
    return "deg" if f.units == "semicircles" else f.units


def dev_field(desc: dict[str, Any], manufacturers: dict[int, str | None]) -> DevField | None:
    """Build a DevField from a decoded field_description; None if it lacks the key parts."""
    index, num = desc.get("developer_data_index"), desc.get("field_definition_number")
    base_type = desc.get("fit_base_type_id")
    if index is None or num is None or base_type is None:
        return None
    name = str(desc.get("field_name") or f"dev_{index}_{num}")
    column = re.sub(r"\W+", "_", name.lower()).strip("_") or f"dev_{index}_{num}"
    return DevField(
        key=(index, num),
        name=name,
        column=column,
        units=desc.get("units"),
        base_type=base_type,
        scale=desc.get("scale"),
        offset=desc.get("offset"),
        manufacturer=manufacturers.get(index),
    )


def iter_messages(
    source: str | PathLike | bytes,
    *,
    raw: bool = False,
    warnings: list[str] | None = None,
) -> Iterator[Message]:
    """Yield every data message in file order.

    raw=False: FIT profile names, physical values, None for invalid values, enum names,
    UTC datetimes, developer fields by name. raw=True: field_<n> keys, raw values with
    sentinels kept, developer fields as bytes under dev_<index>_<num>.

    Recoverable problems (CRC mismatch, truncated file, ...) are appended to `warnings` if
    given, otherwise issued with warnings.warn. Structural problems raise FitParseError.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        buf = bytes(source)
    else:
        buf = Path(source).read_bytes()
    warn = warnings.append if warnings is not None else pywarnings.warn
    return _walk(buf, raw, warn)


def _walk(buf: bytes, raw: bool, warn: Warn) -> Iterator[Message]:
    pos = 0
    while pos < len(buf):  # FIT files can be chained back to back
        pos = yield from _walk_file(buf, pos, _Context(raw, warn))


def _walk_file(buf: bytes, start: int, ctx: _Context) -> Generator[Message, None, int]:
    data_start, data_end, truncated = _read_header(buf, start, ctx.warn)
    if not truncated:
        stored = struct.unpack_from("<H", buf, data_end)[0]
        computed = crc16(buf[start:data_end])
        if computed != stored:
            ctx.warn(
                f"file CRC mismatch: stored {stored:#06x}, computed {computed:#06x} "
                f"at byte {data_end}"
            )

    defs: dict[int, _Definition] = {}
    last_timestamp: int | None = None  # raw field 253 of the latest message, any type
    pos = data_start
    try:
        while pos < data_end:
            header = buf[pos]
            compressed_timestamp = None
            if header & 0x80:  # compressed timestamp header: local type in bits 5-6
                local = (header >> 5) & 0x03
                if last_timestamp is None:
                    raise FitParseError("compressed timestamp before any full timestamp", pos)
                last_timestamp += ((header & 0x1F) - last_timestamp) & 0x1F
                compressed_timestamp = last_timestamp
            elif header & 0x40:
                defs[header & 0x0F], pos = _read_definition(buf, pos, data_end, ctx)
                continue
            else:
                local = header & 0x0F

            d = defs.get(local)
            if d is None:
                raise FitParseError(f"data message for undefined local type {local}", pos)
            end = pos + 1 + d.struct.size
            if end > data_end:
                raise _PastEnd("data message runs past end of data", pos)
            values = d.struct.unpack_from(buf, pos + 1)

            if d.timestamp_item is not None and values[d.timestamp_item] != 0xFFFFFFFF:
                last_timestamp = values[d.timestamp_item]
            fields = _fields(d.slots, values)
            if compressed_timestamp is not None:  # an explicit field 253 would win
                key, value = (
                    ("field_253", compressed_timestamp)
                    if ctx.raw
                    else ("timestamp", _datetime(compressed_timestamp))
                )
                fields = {key: value} | fields
            dev_fields = _fields(d.dev_slots, values, d.n_items) if d.dev_slots else {}
            message = Message(d.num, d.name, fields, dev_fields, pos)
            if not ctx.raw:
                _remember(message, ctx)
            yield message
            pos = end
    except _PastEnd as e:
        if not truncated:
            raise
        ctx.warn(f"file truncated: incomplete message at byte {e.offset}, decoding stopped")
        return len(buf)
    return len(buf) if truncated else data_end + 2


def _remember(message: Message, ctx: _Context) -> None:
    """Keep what later definitions need: the manufacturer and the developer field types."""
    if message.num == FILE_ID:
        ctx.manufacturer = message.fields.get("manufacturer")
    elif message.num == DEVELOPER_DATA_ID:
        index = message.fields.get("developer_data_index")
        ctx.dev_manufacturers[index] = message.fields.get("manufacturer_id")
    elif message.num == FIELD_DESCRIPTION:
        dev = dev_field(message.fields, ctx.dev_manufacturers)
        if dev is None:
            ctx.warn(
                f"field_description without index, number or base type at byte {message.offset}"
            )
        else:
            ctx.dev[dev.key] = dev


def _read_header(buf: bytes, start: int, warn: Warn) -> tuple[int, int, bool]:
    """Validate the file header at `start`; return (data_start, data_end, truncated)."""
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
        warn(
            f"file truncated: header promises {data_size} data bytes + CRC, "
            f"{len(buf) - data_start} bytes present, CRC not checked, at byte {len(buf)}"
        )
        return data_start, min(data_end, len(buf)), True
    return data_start, data_end, False


def _read_definition(buf: bytes, pos: int, data_end: int, ctx: _Context) -> tuple[_Definition, int]:
    """Compile the definition message at `pos`; return it and the next position."""
    if pos + 6 > data_end:
        raise _PastEnd("definition message runs past end of data", pos)
    arch = buf[pos + 2]
    if arch not in (0, 1):
        raise FitParseError(f"unknown architecture byte {arch}", pos + 2)
    endian = "<" if arch == 0 else ">"  # applies to every multi-byte value, the global number too
    num = struct.unpack_from(endian + "H", buf, pos + 3)[0]
    end = pos + 6 + 3 * buf[pos + 5]
    if end > data_end:
        raise _PastEnd("definition message runs past end of data", pos)

    if ctx.raw:
        name, profile = f"mesg_{num}", {}
    else:
        name, profile = profile_fields(num, ctx.manufacturer)
    codes, slots, item, timestamp_item = [endian], [], 0, None
    for i in range(pos + 6, end, 3):
        field_num, size, base_type = buf[i], buf[i + 1], buf[i + 2]
        f = profile.get(field_num)
        code, kind, n = _layout(size, base_type, ctx.warn, f"field {field_num} of message {num}", i)
        if field_num == TIMESTAMP and kind == _SCALAR:
            timestamp_item = item
        key = f.name if f else f"field_{field_num}"
        codes.append(code)
        slots.append((key, kind, n, None if ctx.raw else _converter(base_type, kind, f)))
        item += n

    # Developer fields: (field_num, size, developer_data_index), typed by an earlier
    # field_description. Without one (or in raw mode) they stay bytes under dev_<index>_<num>.
    dev_slots = []
    if buf[pos] & 0x20:
        if end + 1 > data_end:
            raise _PastEnd("definition message runs past end of data", pos)
        dev_start, end = end + 1, end + 1 + 3 * buf[end]
        if end > data_end:
            raise _PastEnd("definition message runs past end of data", pos)
        standard_names = {f.name for f in profile.values()}
        for i in range(dev_start, end, 3):
            field_num, size, index = buf[i], buf[i + 1], buf[i + 2]
            dev = None if ctx.raw else ctx.dev.get((index, field_num))
            if dev is None:
                if not ctx.raw:
                    ctx.warn(
                        f"developer field {index}/{field_num} has no field_description, kept as bytes, at byte {i}"
                    )
                codes.append(f"{size}s")
                dev_slots.append((f"dev_{index}_{field_num}", _BYTES, 1, None))
                continue
            where = f"developer field {dev.name!r}"
            code, kind, n = _layout(size, dev.base_type, ctx.warn, where, i)
            key = f"dev_{dev.column}" if dev.column in standard_names else dev.column
            bt = BASE_TYPES.get(dev.base_type & 0x1F)
            f = Field(key, bt.name if bt else "byte", dev.scale or 1, dev.offset or 0, dev.units)
            codes.append(code)
            dev_slots.append((key, kind, n, _converter(dev.base_type, kind, f)))

    definition = _Definition(
        num,
        name,
        tuple(slots),
        tuple(dev_slots),
        item,
        struct.Struct("".join(codes)),
        timestamp_item,
    )
    return definition, end


def _layout(size: int, base_type: int, warn: Warn, where: str, offset: int) -> tuple[str, int, int]:
    """struct code, kind and number of struct items for one field."""
    base = base_type & 0x1F
    bt = BASE_TYPES.get(base)
    if bt is None:
        warn(f"unknown base type {base_type:#04x} for {where}, kept as bytes, at byte {offset}")
        return f"{size}s", _BYTES, 1
    if base == FIT_STRING:
        return f"{size}s", _TEXT, 1
    if base == FIT_BYTE or size % bt.size:
        return f"{size}s", _BYTES, 1
    if size == bt.size:
        return bt.code, _SCALAR, 1
    n = size // bt.size
    return f"{n}{bt.code}", _ARRAY, n


def _converter(base_type: int, kind: int, f: Field | None) -> Convert | None:
    """raw value -> decoded value: sentinel -> None, then scale/offset, enum or datetime."""
    base = base_type & 0x1F
    if kind == _TEXT:
        return _empty_to_none
    if kind == _BYTES:
        return _all_ff_to_none if base == FIT_BYTE else None
    invalid = BASE_TYPES[base].invalid
    value = _value_converter(f)

    if base in FIT_FLOATS:  # the all-ones sentinel is a NaN, which never compares equal

        def one(v):
            return None if math.isnan(v) else value(v) if value else v
    else:

        def one(v):
            return None if v == invalid else value(v) if value else v

    if kind == _ARRAY:

        def many(vs):
            out = tuple(one(v) for v in vs)
            return None if all(v is None for v in out) else out

        return many
    return one


def _value_converter(f: Field | None) -> Convert | None:
    if f is None:
        return None
    if f.type in ("date_time", "local_date_time"):
        return _datetime
    if f.type in ENUMS:
        names, prefix = ENUMS[f.type], f.type
        return lambda v: names.get(v, f"{prefix}_{v}")
    if f.units == "semicircles":
        return _degrees
    if f.scale != 1 or f.offset != 0:
        scale, offset = f.scale, f.offset
        return lambda v: v / scale - offset
    return None


def _datetime(v: int) -> datetime:
    # local_date_time goes through here too: wall-clock time labelled UTC, so that
    # activity.local_timestamp - activity.timestamp is the UTC offset.
    return datetime.fromtimestamp(v + FIT_EPOCH, tz=UTC)


def _degrees(v: int) -> float:
    return v * SEMICIRCLES_TO_DEGREES


def _empty_to_none(v: str | bytes) -> str | bytes | None:
    return v or None


def _all_ff_to_none(v: bytes) -> bytes | None:
    return v if v.strip(b"\xff") else None


def _fields(slots: tuple, values: tuple, start: int = 0) -> dict[str, Any]:
    fields = {}
    i = start
    for key, kind, n, convert in slots:
        if kind == _ARRAY:
            v = values[i : i + n]
        elif kind == _TEXT:
            v = _text(values[i])
        else:
            v = values[i]
        fields[key] = convert(v) if convert else v
        i += n
    return fields


def _text(raw: bytes) -> str | bytes:
    """UTF-8 up to the first NUL. Undecodable bytes are kept as bytes rather than guessed."""
    raw = raw.split(b"\0", 1)[0]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
