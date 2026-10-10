"""FIT profile data, copied from the FIT SDK Profile.xlsx. Data only, no logic.

A subset: the fields listed in CLAUDE.md plus every field seen in the sample files. The entries
were generated from Garmin's garmin-fit-sdk, and tests/test_profile.py checks them against it.
Anything not listed decodes as field_<n> / mesg_<n>; add it here (from the SDK) when it matters.
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


class Field(NamedTuple):
    name: str
    type: str  # a base type name, date_time / local_date_time, or an enum name from ENUMS
    scale: float = 1  # physical = raw / scale - offset
    offset: float = 0
    units: str | None = None


# num -> (message name, {field_num: Field}). Keyed by (message, field): lap and session share
# names but not numbers.
MESSAGES: dict[int, tuple[str, dict[int, Field]]] = {
    0: ("file_id", {
        0: Field("type", "file"),
        1: Field("manufacturer", "manufacturer"),
        2: Field("product", "uint16"),
        3: Field("serial_number", "uint32z"),
        4: Field("time_created", "date_time"),
        8: Field("product_name", "string"),
    }),
    18: ("session", {
        253: Field("timestamp", "date_time", units="s"),
        2: Field("start_time", "date_time"),
        5: Field("sport", "sport"),
        6: Field("sub_sport", "sub_sport"),
        7: Field("total_elapsed_time", "uint32", 1000, 0, "s"),
        8: Field("total_timer_time", "uint32", 1000, 0, "s"),
        9: Field("total_distance", "uint32", 100, 0, "m"),
        10: Field("total_cycles", "uint32", units="cycles"),
        11: Field("total_calories", "uint16", units="kcal"),
        14: Field("avg_speed", "uint16", 1000, 0, "m/s"),
        15: Field("max_speed", "uint16", 1000, 0, "m/s"),
        16: Field("avg_heart_rate", "uint8", units="bpm"),
        17: Field("max_heart_rate", "uint8", units="bpm"),
        18: Field("avg_cadence", "uint8", units="rpm"),
        19: Field("max_cadence", "uint8", units="rpm"),
        20: Field("avg_power", "uint16", units="watts"),
        21: Field("max_power", "uint16", units="watts"),
        22: Field("total_ascent", "uint16", units="m"),
        23: Field("total_descent", "uint16", units="m"),
        35: Field("training_stress_score", "uint16", 10, 0, "tss"),
        57: Field("avg_temperature", "sint8", units="C"),
        64: Field("min_heart_rate", "uint8", units="bpm"),
        89: Field("avg_vertical_oscillation", "uint16", 10, 0, "mm"),
        91: Field("avg_stance_time", "uint16", 10, 0, "ms"),
        132: Field("avg_vertical_ratio", "uint16", 100, 0, "percent"),
        133: Field("avg_stance_time_balance", "uint16", 100, 0, "percent"),
        134: Field("avg_step_length", "uint16", 10, 0, "mm"),
    }),
    19: ("lap", {
        253: Field("timestamp", "date_time", units="s"),
        254: Field("message_index", "message_index"),
        2: Field("start_time", "date_time"),
        7: Field("total_elapsed_time", "uint32", 1000, 0, "s"),
        8: Field("total_timer_time", "uint32", 1000, 0, "s"),
        9: Field("total_distance", "uint32", 100, 0, "m"),
        11: Field("total_calories", "uint16", units="kcal"),
        13: Field("avg_speed", "uint16", 1000, 0, "m/s"),
        14: Field("max_speed", "uint16", 1000, 0, "m/s"),
        15: Field("avg_heart_rate", "uint8", units="bpm"),
        16: Field("max_heart_rate", "uint8", units="bpm"),
        17: Field("avg_cadence", "uint8", units="rpm"),
        18: Field("max_cadence", "uint8", units="rpm"),
        19: Field("avg_power", "uint16", units="watts"),
        20: Field("max_power", "uint16", units="watts"),
        21: Field("total_ascent", "uint16", units="m"),
        22: Field("total_descent", "uint16", units="m"),
        23: Field("intensity", "intensity"),
        24: Field("lap_trigger", "lap_trigger"),
        25: Field("sport", "sport"),
        39: Field("sub_sport", "sub_sport"),
        50: Field("avg_temperature", "sint8", units="C"),
        63: Field("min_heart_rate", "uint8", units="bpm"),
        77: Field("avg_vertical_oscillation", "uint16", 10, 0, "mm"),
        78: Field("avg_stance_time_percent", "uint16", 100, 0, "percent"),
        79: Field("avg_stance_time", "uint16", 10, 0, "ms"),
        118: Field("avg_vertical_ratio", "uint16", 100, 0, "percent"),
        120: Field("avg_step_length", "uint16", 10, 0, "mm"),
    }),
    20: ("record", {
        253: Field("timestamp", "date_time", units="s"),
        0: Field("position_lat", "sint32", units="semicircles"),
        1: Field("position_long", "sint32", units="semicircles"),
        2: Field("altitude", "uint16", 5, 500, "m"),
        3: Field("heart_rate", "uint8", units="bpm"),
        4: Field("cadence", "uint8", units="rpm"),  # running: one foot, steps/min = 2 x
        5: Field("distance", "uint32", 100, 0, "m"),
        6: Field("speed", "uint16", 1000, 0, "m/s"),
        7: Field("power", "uint16", units="watts"),
        29: Field("accumulated_power", "uint32", units="watts"),
        39: Field("vertical_oscillation", "uint16", 10, 0, "mm"),
        41: Field("stance_time", "uint16", 10, 0, "ms"),
        42: Field("activity_type", "activity_type"),
        53: Field("fractional_cadence", "uint8", 128, 0, "rpm"),
        73: Field("enhanced_speed", "uint32", 1000, 0, "m/s"),
        78: Field("enhanced_altitude", "uint32", 5, 500, "m"),
        83: Field("vertical_ratio", "uint16", 100, 0, "percent"),
        85: Field("step_length", "uint16", 10, 0, "mm"),
    }),
    21: ("event", {
        253: Field("timestamp", "date_time", units="s"),
        0: Field("event", "event"),
        1: Field("event_type", "event_type"),
        3: Field("data", "uint32"),
        4: Field("event_group", "uint8"),
    }),
    23: ("device_info", {
        253: Field("timestamp", "date_time", units="s"),
        0: Field("device_index", "device_index"),
        2: Field("manufacturer", "manufacturer"),
        4: Field("product", "uint16"),
        5: Field("software_version", "uint16", 100, 0),
        27: Field("product_name", "string"),
    }),
    34: ("activity", {
        253: Field("timestamp", "date_time"),
        0: Field("total_timer_time", "uint32", 1000, 0, "s"),
        1: Field("num_sessions", "uint16"),
        2: Field("type", "activity"),
        3: Field("event", "event"),
        4: Field("event_type", "event_type"),
        5: Field("local_timestamp", "local_date_time"),
    }),
    206: ("field_description", {
        0: Field("developer_data_index", "uint8"),
        1: Field("field_definition_number", "uint8"),
        2: Field("fit_base_type_id", "fit_base_type"),
        3: Field("field_name", "string"),
        6: Field("scale", "uint8"),
        7: Field("offset", "sint8"),
        8: Field("units", "string"),
        14: Field("native_mesg_num", "mesg_num"),
        15: Field("native_field_num", "uint8"),
    }),
    207: ("developer_data_id", {
        1: Field("application_id", "byte"),
        2: Field("manufacturer_id", "manufacturer"),
        3: Field("developer_data_index", "uint8"),
        4: Field("application_version", "uint32"),
    }),
}  # fmt: skip

# Enum types: code -> name. An unknown code decodes to "<type>_<code>". manufacturer, sport and
# sub_sport are subsets; the others are complete.
ENUMS: dict[str, dict[int, str]] = {
    "manufacturer": {
        1: "garmin", 23: "suunto", 32: "wahoo_fitness", 95: "stryd", 123: "polar_electro",
        255: "development", 294: "coros",
    },
    "sport": {
        0: "generic", 1: "running", 2: "cycling", 5: "swimming", 10: "training", 11: "walking",
        17: "hiking",
    },
    "sub_sport": {0: "generic", 1: "treadmill", 2: "street", 3: "trail", 4: "track", 45: "indoor_running"},
    "file": {
        1: "device", 2: "settings", 3: "sport", 4: "activity", 5: "workout", 6: "course",
        7: "schedules", 9: "weight", 10: "totals", 11: "goals", 14: "blood_pressure",
        15: "monitoring_a", 20: "activity_summary", 28: "monitoring_daily", 32: "monitoring_b",
        34: "segment", 35: "segment_list", 40: "exd_configuration", 247: "mfg_range_min",
        254: "mfg_range_max",
    },
    "activity": {0: "manual", 1: "auto_multi_sport"},
    "activity_type": {
        0: "generic", 1: "running", 2: "cycling", 3: "transition", 4: "fitness_equipment",
        5: "swimming", 6: "walking", 8: "sedentary", 13: "wheelchair_pushing", 254: "all",
    },
    "event": {
        0: "timer", 3: "workout", 4: "workout_step", 5: "power_down", 6: "power_up",
        7: "off_course", 8: "session", 9: "lap", 10: "course_point", 11: "battery",
        12: "virtual_partner_pace", 13: "hr_high_alert", 14: "hr_low_alert",
        15: "speed_high_alert", 16: "speed_low_alert", 17: "cad_high_alert", 18: "cad_low_alert",
        19: "power_high_alert", 20: "power_low_alert", 21: "recovery_hr", 22: "battery_low",
        23: "time_duration_alert", 24: "distance_duration_alert", 25: "calorie_duration_alert",
        26: "activity", 27: "fitness_equipment", 28: "length", 32: "user_marker",
        33: "sport_point", 36: "calibration", 42: "front_gear_change", 43: "rear_gear_change",
        44: "rider_position_change", 45: "elev_high_alert", 46: "elev_low_alert",
        47: "comm_timeout", 54: "auto_activity_detect", 56: "dive_alert", 57: "dive_gas_switched",
        71: "tank_pressure_reserve", 72: "tank_pressure_critical", 73: "tank_lost",
        75: "radar_threat_alert", 76: "tank_battery_low", 81: "tank_pod_connected",
        82: "tank_pod_disconnected",
    },
    "event_type": {
        0: "start", 1: "stop", 2: "consecutive_depreciated", 3: "marker", 4: "stop_all",
        5: "begin_depreciated", 6: "end_depreciated", 7: "end_all_depreciated",
        8: "stop_disable", 9: "stop_disable_all",
    },
    "intensity": {
        0: "active", 1: "rest", 2: "warmup", 3: "cooldown", 4: "recovery", 5: "interval",
        6: "other",
    },
    "lap_trigger": {
        0: "manual", 1: "time", 2: "distance", 3: "position_start", 4: "position_lap",
        5: "position_waypoint", 6: "position_marked", 7: "session_end", 8: "fitness_equipment",
    },
}  # fmt: skip

# Vendor quirks: (file_id.manufacturer, mesg_num) -> extra fields, applied only when the file's
# manufacturer matches. Standard profile fields win. Empty for now: the one candidate, Coros
# session field 91 ("training load"), is the standard avg_stance_time (see NOTES.md).
VENDOR_FIELDS: dict[tuple[str, int], dict[int, Field]] = {}
