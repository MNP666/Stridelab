"""L2: Activity groups the message stream and offers convenience views.

Everything here is a view over `messages`; nothing re-parses. *_df() return pandas.
"""

from collections import defaultdict
from functools import cached_property
from os import PathLike
from typing import Any

import pandas as pd

from .reader import (
    DEVELOPER_DATA_ID,
    FIELD_DESCRIPTION,
    FILE_ID,
    DevField,
    Message,
    dev_field,
    iter_messages,
    output_units,
    profile_fields,
)

SESSION, LAP, RECORD, EVENT, DEVICE_INFO, ACTIVITY = 18, 19, 20, 21, 23, 34


def read(source: str | PathLike | bytes, *, raw: bool = False) -> "Activity":
    """Decode a .fit file (path or bytes) into an Activity. Warnings end up in .warnings."""
    warnings: list[str] = []
    messages = list(iter_messages(source, raw=raw, warnings=warnings))
    return Activity(messages, warnings, raw=raw)


class Activity:
    def __init__(
        self, messages: list[Message], warnings: list[str] | None = None, *, raw: bool = False
    ):
        self.messages = messages  # every message in file order (escape hatch)
        self.warnings = warnings if warnings is not None else []
        self.raw = raw
        self._by_num: dict[int, list[Message]] = defaultdict(list)
        for m in messages:
            self._by_num[m.num].append(m)

    def __repr__(self) -> str:
        parts = [
            f"{len(self.records)} records",
            f"{len(self.laps)} laps",
            f"{len(self.warnings)} warnings",
        ]
        if self.raw:
            parts.insert(0, "raw")
        else:
            start = self.session.get("start_time")
            device = self.file_id.get("product_name") or self.file_id.get("manufacturer")
            parts[:0] = [
                str(device),
                str(self.session.get("sport")),
                f"{start:%Y-%m-%d %H:%M}Z" if start else "?",
            ]
        return f"<Activity {', '.join(parts)}>"

    def select(self, num: int) -> list[dict[str, Any]]:
        """Every message with global number `num` as a dict, developer fields merged in."""
        return [m.fields | m.dev_fields for m in self._by_num.get(num, [])]

    def _first(self, num: int) -> dict[str, Any]:
        found = self.select(num)
        return found[0] if found else {}

    @cached_property
    def file_id(self) -> dict[str, Any]:
        return self._first(FILE_ID)

    @cached_property
    def sessions(self) -> list[dict[str, Any]]:
        return self.select(SESSION)

    @cached_property
    def session(self) -> dict[str, Any]:
        return self.sessions[0] if self.sessions else {}

    @cached_property
    def device_info(self) -> dict[str, Any]:
        """The creator device (device_index 0) if marked, else the first device_info."""
        devices = self.select(DEVICE_INFO)
        creator = [d for d in devices if d.get("device_index") == 0]
        return (creator or devices or [{}])[0]

    @cached_property
    def records(self) -> list[dict[str, Any]]:
        return self.select(RECORD)

    @cached_property
    def laps(self) -> list[dict[str, Any]]:
        return self.select(LAP)

    @cached_property
    def events(self) -> list[dict[str, Any]]:
        return self.select(EVENT)

    @cached_property
    def dev_fields(self) -> list[DevField]:
        """One DevField per (developer_data_index, field number); a later description wins."""
        if self.raw:
            return []
        manufacturers, found = {}, {}
        for m in self.messages:
            if m.num == DEVELOPER_DATA_ID:
                manufacturers[m.fields.get("developer_data_index")] = m.fields.get(
                    "manufacturer_id"
                )
            elif m.num == FIELD_DESCRIPTION and (dev := dev_field(m.fields, manufacturers)):
                found[dev.key] = dev
        return list(found.values())

    @cached_property
    def units(self) -> dict[str, str]:
        """Units of the decoded values by key, e.g. {"step_length": "mm", "form_power": "Watts"}."""
        if self.raw:
            return {}
        units: dict[str, str] = {}
        manufacturer = self.file_id.get("manufacturer")
        for num in self._by_num:
            for f in profile_fields(num, manufacturer)[1].values():
                if u := output_units(f):
                    units.setdefault(f.name, u)
        for dev in self.dev_fields:
            if dev.units:
                units.setdefault(dev.column, dev.units)
        return units

    def summary(self) -> dict[str, Any]:
        """A flat dict for a one-screen overview. Derived keys carry a unit suffix."""
        self._need_profile("summary()")
        s, activity = self.session, self._first(ACTIVITY)
        utc_offset = None
        if activity.get("local_timestamp") and activity.get("timestamp"):
            utc_offset = activity["local_timestamp"] - activity["timestamp"]
        return {
            "device": self.file_id.get("product_name") or self.device_info.get("product_name"),
            "manufacturer": self.file_id.get("manufacturer"),
            "sport": s.get("sport"),
            "sub_sport": s.get("sub_sport"),
            "start": s.get("start_time"),
            "utc_offset": utc_offset,
            "distance_m": s.get("total_distance"),
            "timer_time_s": s.get("total_timer_time"),
            "elapsed_time_s": s.get("total_elapsed_time"),
            "avg_heart_rate_bpm": s.get("avg_heart_rate"),
            "max_heart_rate_bpm": s.get("max_heart_rate"),
            "avg_speed_m_per_s": s.get("avg_speed"),
            "max_speed_m_per_s": s.get("max_speed"),
            "avg_cadence_rpm": s.get("avg_cadence"),
            "avg_power_w": s.get("avg_power"),
            "total_ascent_m": s.get("total_ascent"),
            "total_descent_m": s.get("total_descent"),
            "total_calories_kcal": s.get("total_calories"),
            "records": len(self.records),
            "laps": len(self.laps),
            "dev_fields": [d.column for d in self.dev_fields],
            "warnings": list(self.warnings),
        }

    def records_df(self) -> pd.DataFrame:
        """One row per record: UTC DatetimeIndex, one column per field, plus elapsed_s.

        speed/altitude are filled from enhanced_speed/enhanced_altitude (which win).
        elapsed_s is wall-clock seconds since the first record, so pauses are included.
        """
        self._need_profile("records_df()")
        df = pd.DataFrame(self.records)
        for col in ("speed", "altitude"):
            enhanced = f"enhanced_{col}"
            if enhanced in df:
                df[col] = df[enhanced].combine_first(df[col]) if col in df else df[enhanced]
        if "timestamp" in df:
            df = df.set_index(pd.DatetimeIndex(df.pop("timestamp"), name="timestamp"))
            df["elapsed_s"] = (df.index - df.index[0]).total_seconds()
        df.attrs["units"] = self._units_for(df) | ({"elapsed_s": "s"} if "elapsed_s" in df else {})
        return df

    def laps_df(self) -> pd.DataFrame:
        """One row per lap, in message_index order."""
        self._need_profile("laps_df()")
        df = pd.DataFrame(self.laps)
        if "message_index" in df:
            df = df.sort_values("message_index", ignore_index=True)
        df.attrs["units"] = self._units_for(df)
        return df

    def _units_for(self, df: pd.DataFrame) -> dict[str, str]:
        return {c: self.units[c] for c in df.columns if c in self.units}

    def _need_profile(self, what: str) -> None:
        if self.raw:
            raise ValueError(f"{what} needs profile names; read the file with raw=False")
