# Slice 01: read a .fit file from Python

> **Status:** done on 2026-10-10. All milestones are ticked below; findings and decisions are in [NOTES.md](../NOTES.md).

**Goal:** one call turns a `.fit` file into Python objects, and the numbers match the verified values in [CLAUDE.md](../CLAUDE.md) for all three sample files.

```python
import stridelab as sl

act = sl.read("data/raw/long_run.fit")
act.summary()        # {'device': 'COROS PACE Pro', 'sport': 'running', 'distance_m': 30385.53, ...}
act.session["total_timer_time"]   # 9823.74
df = act.records_df()             # 9825 rows, UTC DatetimeIndex, form_power and step_length as columns
```

This is a *vertical* slice: thin, but it goes all the way from bytes, through `Message` and `Activity`, to a DataFrame and a notebook. Analysis comes in later slices.

**Out of scope for this slice:**
- `smoothing.py`, `segments.py` and HR zones
- `field_stats()`, `--scan` and `--csv`
- the `strict=` flag. Until then, hard rule 5 applies as written: structural errors raise, and CRC problems or a truncated tail become warnings.
- typed dataclasses for messages; plain dicts are enough for now
- anything in `Profile.xlsx` beyond the subset listed in CLAUDE.md

## Milestones

| # | Milestone | Main files | Afterwards you can... |
|---|---|---|---|
| M0 | Skeleton | `pyproject.toml`, `src/stridelab/__init__.py`, `tests/conftest.py` | `import stridelab` and run `pytest` |
| M1 | Raw message walk | `reader.py`, `profile.py` (base types only), `tests/fitwriter.py` | iterate every message in every sample file as field numbers and raw values |
| M2 | Profile | `profile.py`, `reader.py` | get names, scaled values, units, enums and UTC datetimes |
| M3 | Developer fields | `reader.py` | read Stryd fields by name (`form_power`, ...) |
| M4 | Activity API | `activity.py`, `__init__.py` | use `sl.read()`, `summary()`, `records_df()` and `laps_df()` |
| M5 | Demo | `__main__.py`, `notebooks/first_look.py`, `NOTES.md` | see the whole slice working end to end |
| M6 | Hardening | `tests/fitwriter.py`, `tests/test_reader.py` | trust the code paths the sample files never exercise |

Each milestone ends with `uv run pytest` passing, `ruff` clean, and a commit.

M1 to M3 grow one decoder. There is never a second byte walker (hard rule 2). Code paths the sample files don't use, such as compressed timestamps, **raise `FitParseError`** until M6 implements and tests them. Nothing stays silently unsupported.

---

## M0: Skeleton

- [x] Add a build backend to `pyproject.toml`, so that `uv sync` installs the package in editable mode:
  ```toml
  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.pytest.ini_options]
  testpaths = ["tests"]

  [tool.ruff]
  line-length = 100
  extend-exclude = ["*.md"]  # otherwise ruff reflows the code blocks in the docs
  ```
  While you're in there, replace `description = "Add your description here"`.
- [x] Delete `main.py` and create `src/stridelab/__init__.py`.
- [x] Copy the sample `.fit` files into `data/raw/`, and `hr_zones.toml` to the repo root. The TOML is for scripts and notebooks; the package never reads it.
- [x] Add `tests/conftest.py` with a `fit_file` fixture that returns `data/raw/<name>.fit`, and calls `pytest.skip` if the file is missing. Golden tests use it. Synthetic tests don't need it.
- [x] Create `NOTES.md` with a dated first entry.

**Done when:** `uv sync` succeeds, `uv run python -c "import stridelab; print(stridelab.__file__)"` prints a path under `src/`, and `uv run pytest` runs, even with zero tests.

---

## M1: Raw message walk

At the end of this milestone, `iter_messages(source, raw=True)` yields one `Message` per data message, with keys `field_<n>`, raw integers, and sentinels kept. This is the same output as `read(path, raw=True)` later on.

**Build**
- [x] Add `FitParseError(msg, offset)` to `reader.py`. Its `str()` should include the offset: `"... at byte 6454"`.
- [x] Add `@dataclass(slots=True) class Message` with `num, name, fields, dev_fields, offset`. For now `name` is `mesg_<num>`.
- [x] Add `BASE_TYPES` to `profile.py`, copied from the table in CLAUDE.md. Look entries up by `base_type & 0x1F`. Each entry holds name, struct code, size and invalid value.
- [x] Parse the **file header**: size (12 or 14), data size, `.FIT` signature, and the header CRC if it is non-zero. The data runs over `[hdr, hdr + data_size)`, followed by a 2-byte CRC.
- [x] Check the **file CRC**. FIT's CRC is plain CRC-16/ARC (polynomial 0xA001 reflected, initial value 0). A 256-entry table takes about 20 ms on `long_run.fit`, against about 75 ms for the SDK's nibble loop (both measured). Running it over data plus the stored CRC gives 0. A mismatch is appended to `warnings`; it does not raise.
- [x] Dispatch on the **record header**: bit 7 means a compressed timestamp, which raises `FitParseError("compressed timestamp header not supported yet", offset)` for now. Bit 6 means a definition, bit 5 means it has dev fields, and bits 0–3 are the local type.
- [x] For a **definition**, read the arch byte and choose `"<"` or `">"`. That choice applies to the global number too. Build **one `struct.Struct` per definition**:
  - a normal field becomes its code
  - an array (size is a multiple of the base size) becomes the code repeated n times, regrouped into a tuple after unpacking
  - an odd size becomes `f"{size}s"`, kept as raw bytes, and so does the `byte` base type
  - a string becomes `f"{size}s"`, decoded as UTF-8 up to the first NUL. Raw mode keeps `""`, its sentinel; M2 turns it into `None`.
  - an unknown base type becomes `f"{size}s"`, kept as raw bytes, plus a warning

  A new definition for a local type replaces the old one.
- [x] **Dev fields in definitions** (bit 5): read `n_dev × (field_num, size, dev_data_index)` and append `f"{size}s"` per dev field to the same struct. The values land in `Message.dev_fields` as raw bytes under `dev_<index>_<num>`, and M3 names and decodes them. *(The first version of this plan said to raise here, but all three sample files define dev fields, so the golden walk needs at least this much.)*
- [x] For a **data message**, unpack with the compiled struct and yield a `Message`. A data message for an undefined local type raises `FitParseError`.
- [x] The walk must end **exactly** at `hdr + data_size`. Overshooting is an error, not a `break`.

**Tests**
- [x] Golden: count messages by global number for each file. long_run should have 9825 record (20), 31 lap (19), 10 event (21) and 1 session (18). intervals_example should have 3237/15/6/1, and treadmill 3558/18/4/1. The set of global numbers should be a subset of {0, 18, 19, 20, 21, 23, 34, 206, 207}.
- [x] Golden: all three files have a valid CRC and no warnings.
- [x] Golden: dev-field keys per message, matching CLAUDE.md's table (long_run records have 5, intervals 1, treadmill 3; session and laps carry 1). Also check some raw session values on long_run (`field_9 == 3038553`, which is 30385.53 m).
- [x] Synthetic: start `tests/fitwriter.py` with a header, definitions (including dev fields), data messages and a CRC, taking an `endian` parameter. Write a few messages in **both** endiannesses and read back identical raw values. Tests can `import fitwriter` directly, because pytest puts `tests/` on `sys.path`.
- [x] Synthetic: structural errors (header size, signature, truncated file, message past the end, undefined local type, architecture byte, compressed header) raise `FitParseError` with the right offset. CRC mismatches are warnings.

**Done when:** the walk reaches the last byte of all three files. Write the time for `list(iter_messages(...))` on long_run in NOTES.md (CLAUDE.md estimates about 0.12 s; measured 31 ms including the CRC).

---

## M2: Profile

At the end of this milestone, `iter_messages(source)` (with `raw=False`, the default) yields FIT names, physical values, `None` for invalid values, enum strings and UTC datetimes.

**Build**
- [x] Fill in `profile.py` with the subset from CLAUDE.md. Include `MESSAGES = {num: (name, {field_num: Field(name, type, scale, offset, units)})}` and `ENUMS` for manufacturer, sport, sub_sport, event and event_type. Key fields by **(message, field)**, never by field number alone. *(Done as: the CLAUDE.md subset plus every field the sample files contain, generated from Garmin's `garmin-fit-sdk`. `tests/test_oracle.py` checks every entry, every enum code and every decoded value against the SDK.)*
- [x] Decode each field in this order:
  1. Check the sentinel on the **raw** value and turn it into `None`. z-types treat 0 as invalid. For floats, treat NaN as invalid, because the all-ones bit pattern is NaN and NaN never compares equal.
  2. Apply scale and offset: `raw / scale - offset`. For arrays, do this element by element.
  3. Convert types:
     - `date_time` becomes a tz-aware UTC `datetime`, using `+ 631_065_600`.
     - Enums become their name, or `"<enum>_<n>"` if unknown.
     - `local_timestamp` is local wall-clock time, not UTC. Convert it the same way, so that `local_timestamp - timestamp` gives the UTC offset.
- [x] An unknown message becomes `mesg_<num>`. An unknown field becomes `field_<num>`, with its sentinel removed but no scale applied.
- [x] `VENDOR_FIELDS`: apply vendor fields only when `file_id.manufacturer` matches. The reader has already seen `file_id` by then, so this is a lookup and not decoder logic. *(The mechanism works and is tested, but the table is empty. The planned entry, Coros session field 91 as "training load", turned out to be the standard `avg_stance_time`; see NOTES.md.)*

**Tests**
- [x] Golden, long_run session: total_distance 30385.53, total_timer_time 9823.74, total_elapsed_time 10151.56, avg/max_heart_rate 143/162, avg/max_speed 3.093/3.846, total_calories 2330, total_ascent/descent 218/226, avg_power 252, avg_cadence 91. Use `pytest.approx`.
- [x] Golden: `file_id["manufacturer"] == "coros"`. Every `timestamp` is tz-aware UTC, and the first record is not before `session["start_time"]`.
- [x] Golden: lap and session use different numbers for the same name. Lap `avg_speed` is field 13 and session `avg_speed` is field 14, and both decode as m/s.
- [x] Golden: treadmill `session["sub_sport"] == "indoor_running"` (code 45, now in the profile). The unknown-enum path is tested synthetically instead.
- [x] Synthetic: a sentinel turns into `None` and a 0 stays 0, a uint16z 0 turns into `None`, an unknown field shows up as `field_<n>`, and altitude decodes correctly with scale 5 and offset 500.

---

## M3: Developer fields

**Build**
- [x] `field_description` (206) and `developer_data_id` (207) go through the normal decode path. The reader updates `{(developer_data_index, field_definition_number): DevField}` as they pass.
- [x] M1 already reads dev fields from definitions into the same compiled struct, as raw bytes. Now compile them with the **base type from the field description** (arrays, strings and sentinels as for standard fields). Descriptions come before the definitions that use them, so this happens when the definition is compiled.
- [x] Name dev fields with the snake_case form of `field_name` (`"Form Power"` becomes `form_power`). Use a `dev_` prefix only if the name collides with a standard field in that message. Apply scale and offset, and remove sentinels, exactly as for standard fields.
- [x] Each `DevField` carries `key, name, column, units, base_type, scale, offset, manufacturer`, where manufacturer comes from 207 via `developer_data_index`.
- [x] A dev field with no description: keep it as raw bytes under `dev_<index>_<num>` and add a warning. Don't drop it (rule 3).

**Tests**
- [x] Golden: long_run records have form_power, leg_spring_stiffness, air_power, impact_loading_rate (sint32) and effort_pace. intervals_example has only effort_pace. treadmill has form_power, leg_spring_stiffness and impact_loading_rate. Session and laps each carry one dev field.
- [x] Golden: the units for form_power are `"Watts"`.
- [x] Synthetic: a dev field with scale and offset, a dev field with a sentinel value, a dev field whose name collides with a standard field (gets the `dev_` prefix), and a big-endian dev field.

---

## M4: Activity API

**Build** (`activity.py` can import pandas at the top)
- [x] `read(source, *, raw=False) -> Activity`. `source` is a path or bytes. It is exported from `__init__.py` together with `iter_messages`, `Activity`, `Message` and `FitParseError`.
- [x] Activity attributes:
  - `messages` (all of them, in file order) and `warnings`
  - `file_id` and `session` as dicts, plus `sessions` if there is more than one
  - `device_info`: the creator device (`device_index` 0) if marked, else the first one. The Coros files have a single device_info without `device_index`.
  - `records`, `laps` and `events` as `list[dict]`, with dev fields merged in
  - `dev_fields`, and `units` covering standard and dev fields
- [x] `summary()` returns a flat dict: device, sport, sub_sport, start, distance, timer and elapsed time, average and max HR, speed, cadence and power, plus `warnings` if there are any. Derived keys carry unit suffixes (`distance_m`, `timer_time_s`).
- [x] `records_df()`:
  - one column per field, with a UTC `DatetimeIndex` from `timestamp`
  - `speed`/`altitude` filled from `enhanced_speed`/`enhanced_altitude`, with the enhanced value winning
  - `elapsed_s` measured from the first record (wall-clock time, so pauses are included)
  - `df.attrs["units"]` set
- [x] `laps_df()`: one row per lap, ordered by `message_index`.

**Tests**
- [x] Golden: long_run `records_df()` has 9825 rows, a monotonic index in UTC, and exactly 4 gaps over 1 s, the longest being 139 s.
- [x] Golden **physics checks**, on rows where `step_length > 0` (these validate the scales for fields 39, 83 and 85):
  - the median of `|vertical_ratio − 100 · vertical_oscillation / step_length|` is under 0.2
  - the median `step_length` is within 2% of `speed / (2 · cadence / 60) · 1000`
- [x] Golden: in intervals_example, laps 4, 6, 8, 10 and 12 have `avg_speed` over 4.0 m/s, and laps 5, 7, 9 and 11 last about 150 s. CLAUDE.md counts laps from 1, so check this against `message_index` first.
- [x] Golden: treadmill has no non-null `position_lat` and no `step_length`.
- [x] Golden: `summary()` for long_run matches the session values from M2.

**Done when:** `sl.read("data/raw/long_run.fit")` finishes in **under 1 s**. Record the time in NOTES.md, because it answers the "compiled core?" open question for now.

---

## M5: Demo

- [x] `__main__.py`: `uv run python -m stridelab data/raw/long_run.fit` prints `summary()` as aligned `key: value` lines, then any warnings. Use argparse with a single positional argument.
- [x] `notebooks/first_look.py` (marimo):
  - read a file chosen from `data/raw/` with a `mo.ui.dropdown`
  - show `summary()` and `dev_fields`
  - plot HR, speed, cadence, step_length and form_power against `elapsed_s`
  - build the same frame in polars with `pl.DataFrame(act.records, infer_schema_length=None)`, then reproduce one plot from it as a first polars exercise
- [x] NOTES.md: an entry for the slice, covering timings, the decisions listed below, and anything that surprised you.

---

## M6: Hardening (synthetic, via `fitwriter.py`)

The sample files never exercise these paths, so each one gets a test built by the writer.

- [x] **Compressed timestamps:** implement `ts = last + ((offset - last) & 0x1F)` and remove the M1 error. Test three things: a rollover (the offset is smaller than `last & 0x1F`), `last` being set from field 253 of *any* message, and each compressed message updating `last`.
- [x] **Chained files:** two FIT files back to back are read as one message stream. *(done in M1)*
- [x] **12-byte header** and a header CRC of 0. *(done in M1)*
- [x] **CRC mismatch:** produces a warning, and the messages are still returned. Covers both the file CRC and the header CRC. *(done in M1)*
- [x] **Truncated tail:** produces a warning with the offset, and the messages before it are returned. A file missing only its CRC also gets a warning.
- [x] **Structural errors** raise `FitParseError` with the right offset: a bad signature, data for an undefined local type, and a definition running past the end of the data. *(done in M1)*
- [x] **Field shapes:** an array, an odd-sized field (kept as bytes), a NUL-padded string, an empty string (becomes `None`), a float NaN (becomes `None`), an array that is partly or entirely invalid, and a `byte` field of all 0xFF.
- [x] **Redefinition:** a second definition for the same local type replaces the first. *(done in M1)*

---

## Decisions made along the way (details in NOTES.md)

- **Semicircles:** the reader converts them to degrees, with units `deg`, the same way it turns `date_time` into datetimes.
- **Arrays with some invalid elements:** `None` per element, and `None` for the whole field if every element is invalid.
- **`device_info`:** the creator (`device_index` 0) if marked, else the first. The Coros files have one device_info and no `device_index`.
- **Dev field without a description:** kept as raw bytes plus a warning. It never happens in the sample files.
- **`elapsed_s`:** wall-clock time from the first record, so pauses are included. Timer time belongs to segments in a later slice.

## Slice 01 is done when

- [x] `sl.read()` works on all three sample files with no warnings, and in under 1 s on long_run
- [x] all golden tests from M1 to M4 pass, and all synthetic tests from M1 to M3 and M6 pass
- [x] `python -m stridelab` prints a summary, and `notebooks/first_look.py` runs top to bottom
- [x] `import stridelab` works on a fresh clone with an empty `data/raw/`, with the golden tests skipped rather than failing
- [x] NOTES.md records the timings and the decisions above
