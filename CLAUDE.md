# Stridelab

Lightweight pure-Python successor to the FitTrackz Rust project. It decodes `.fit` activity files (standard **and** developer fields) with **its own decoder, not a third-party FIT library**, then you experiment with smoothing and segmentation in marimo notebooks, using pandas or polars. The purpose is exploration: finding out which analyses are worth keeping. Optimise for clarity and fast iteration, not completeness or speed.

## Commands

```bash
uv sync                                                  # create .venv, install deps + dev group + the package (editable)
uv add <pkg>                                             # runtime dep; `uv add --dev <pkg>` for tooling and notebook-only libs
uv run pytest -q                                         # golden tests skip if data/raw/*.fit is missing
uv run ruff check --fix . && uv run ruff format .
uv run python -m stridelab data/raw/long_run.fit         # one-screen summary
uv run python -m stridelab data/raw/long_run.fit --scan  # per-field stats, incl. unknown field_<n>
uv run marimo edit notebooks/<name>.py                   # open or create a notebook
uv run python scripts/<name>.py --help
```

The project uses **uv** for everything: run code with `uv run`, and change dependencies with `uv add` / `uv remove`, never `pip install`. Commit `pyproject.toml` and `uv.lock` together. Python ≥ 3.12 (pinned in `.python-version`). src layout with a `[build-system]`, so `uv sync` installs `stridelab` in editable mode.

**Dependency split.** Runtime dependencies (numpy, pandas, matplotlib) are the only third-party packages `src/` may import. The dev group (polars, marimo, pytest, ruff) is for notebooks, scripts and tests, and `src/` never imports it.

## Hard rules

1. **We parse FIT ourselves.** No third-party FIT library (`fitparse`, `fitdecode`, `garmin-fit-sdk`, ...) is ever a dependency of the package or imported by it. The decoder (`reader.py`, `profile.py`) uses only the stdlib (`struct`), so the byte layout stays readable in one place. Ordinary dependencies (numpy, pandas, matplotlib, scipy) are fine everywhere above the decoder.
2. **One generic decoder, one forward pass.** Records, laps, session, metadata and scan are all views over the same `Message` stream. Never write a second special-purpose byte walker. The Rust version had four copy-pasted ones.
3. **Decode everything, drop nothing.** Unknown messages become `mesg_<num>`. Unknown fields become `field_<num>` with the raw value. Developer fields are kept by name. Choosing channels is the analysis layer's job.
4. The parser never smooths, filters, resamples or renames. Analysis functions are pure (DataFrame/Series in → out) and never re-parse.
5. **Fail loudly, with location.** Structural errors raise `FitParseError(msg, offset)`. Recoverable problems (CRC mismatch, truncated tail) are appended to `activity.warnings`. No silent `break`.
6. FIT names, scales, units and enum codes come from the FIT SDK `Profile.xlsx`. **Never infer a field's meaning from its value range.** That is how `vertical_ratio` got labelled "stride_height (mm)" in the Rust version.

## Layout

```
src/stridelab/
  __init__.py   read(), iter_messages(), Activity, Message, FitParseError
  reader.py     L1  bytes → Message stream: header, CRC, definitions, compressed ts, dev fields
  profile.py    L1  data only: BASE_TYPES, MESSAGES {num: (name, {field_num: Field})}, ENUMS, VENDOR_FIELDS
  activity.py   L2  Activity: groups messages, convenience accessors, *_df() (pandas)
  smoothing.py  L3  Series → Series smoothers
  segments.py   L3  moving/stopped detection, segment ids, laps → intervals, HR zones
  __main__.py       tiny CLI: summary | --scan | --csv
tests/          fitwriter.py (synthetic FIT encoder), conftest.py (data paths, skip if missing)
                + test_reader / test_activity / test_smoothing
notebooks/      marimo notebooks: interactive exploration, one question per notebook
scripts/        non-interactive, repeatable jobs: figures, exports, benchmarks, ginput fallback
data/raw/       source .fit files, never modified (gitignored); copy from FitTrackz data/raw/
data/derived/   anything generated: csv / parquet / png (gitignored, safe to delete)
hr_zones.toml   personal HR zones, read by scripts and notebooks (never by the package)
NOTES.md        dated experiment log: tried → result → decision
```

- **Promotion:** notebook → (optionally) script → `src/`. Code moves into the package only after it has been reused in two places and has a test. Notebooks and scripts import `stridelab`, never each other.
- **Notebooks (marimo):** each global name can be defined in only one cell, so prefix cell-local temporaries with `_`. Find data with `mo.notebook_dir().parent / "data" / "raw"` so the notebook works from any cwd. Run notebooks in the project env, never with `--sandbox`, which builds a separate env without the local `stridelab`. A cell displays its last expression, so end plotting cells with the figure. `plt.ginput` needs a desktop window, so manual interval clicking belongs in `scripts/`.
- **Scripts:** argparse, where only the input file is positional and every experiment parameter is a `--flag`. Write outputs to `data/derived/`. Find the repo root with `Path(__file__).resolve().parents[1]`.

## API shape

```python
import stridelab as sl
act = sl.read("data/raw/long_run.fit")   # path or bytes; strict=True, raw=False
act.summary()                            # flat dict: device, sport, start, distance, timer time, HR, ...
act.file_id, act.session, act.device_info     # dict[str, Any] with FIT profile names (act.sessions if >1)
act.records, act.laps, act.events        # list[dict]; dev fields merged in
act.dev_fields                           # [DevField(key, name, column, units, base_type, scale, offset, manufacturer)]
act.units                                # {"step_length": "mm", "form_power": "Watts", ...}
act.messages                             # every Message in file order (escape hatch)
act.warnings                             # list[str]
act.field_stats("record")                # count / none / zeros / min / max / samples per field (replaces scan + dump)
df = act.records_df()                    # UTC DatetimeIndex, one column per field, + elapsed_s; df.attrs["units"]
laps = act.laps_df()
sl.read(path, raw=True)                  # no profile: field_<n> keys, raw ints, sentinels kept
```

- **pandas is the package's DataFrame library.** All `*_df()` methods and L3 functions take and return pandas, so there is only one dialect in `src/`. Polars is for learning in notebooks: build frames straight from the dicts with `pl.DataFrame(act.records, infer_schema_length=None)`. Without that argument, a column that is None for the first 100 rows (paused GPS, late dev fields) makes construction fail. `pl.from_pandas` needs pyarrow for tz-aware and string columns, and pyarrow is not installed. Doing the same analysis in both dialects in one notebook is encouraged, and is a good check.

- `Message` is a `@dataclass(slots=True)` with fields `num, name, fields: dict, dev_fields: dict, offset`.
- Keep messages as plain dicts while exploring. Freeze a typed dataclass only once its shape stops changing.
- **Names:** use FIT profile names verbatim (`step_length`, `total_timer_time`) so they can be grepped in the SDK. A developer field becomes the snake_case form of its `field_name` (`"Form Power"` → `form_power`), prefixed with `dev_` only if that collides with a standard name. Columns we derive carry a unit suffix (`cadence_spm`, `pace_s_per_km`, `elapsed_s`).
- **Time:** the reader turns every `date_time` field into a tz-aware UTC `datetime`, so the FIT epoch never leaks out of the reader (except with `raw=True`). The UTC offset is `activity.local_timestamp − activity.timestamp`.
- **Missing data:** invalid sentinels become `None` (NaN in DataFrames). Zeros are kept as zeros. In running dynamics a 0 often means "standing still", and the analysis layer decides how to treat it.
- `records_df()` fills `speed`/`altitude` from `enhanced_speed`/`enhanced_altitude` when present, with the enhanced value winning.

## FIT format: what the reader must handle

- **File header:** `[0]` size (12 or 14), `[4:8]` data size (LE), `[8:12]` `b".FIT"`, `[12:14]` header CRC (0 means unset). Data spans `[hdr, hdr + data_size)` and is followed by a CRC-16 over header + data. Files can be chained, i.e. another header may follow the CRC.
- **Record header with bit 7 = 0:** bit 6 marks a definition, bit 5 means "has dev fields" (definitions only), bits 0–3 are the local type (0–15).
  **With bit 7 = 1 (compressed timestamp):** bits 5–6 are the local type (0–3) and bits 0–4 a time offset. The payload is a normal data message of that local type.
- **Definition message:** reserved byte, arch byte (0 = LE, 1 = BE; applies to *every* multi-byte value in that message, including floats and the global number), global number (u16), then n × `(field_num, size, base_type)`. If bit 5 is set, n_dev × `(field_num, size, dev_data_index)` follows. A new definition for a local type replaces the old one.
- **Data message:** standard field bytes in definition order, then dev field bytes. There are no tags; the layout comes only from the definition. Compiling one `struct.Struct` per definition is the only optimisation worth doing early.
- **Field sizes:** size > base size means an array (tuple); a size that isn't a multiple of the base size is kept as raw bytes. Strings are UTF-8 up to the first NUL, and an empty string becomes None.
- **Compressed timestamps:** `ts = last + ((offset - last) & 0x1F)`. Here `last` is the most recent field 253 from **any** message, and each compressed message also updates it.
- **Developer fields:** `field_description` (206) maps `(developer_data_index, field_definition_number)` to name, base type, units and scale/offset. `developer_data_id` (207) identifies the app and manufacturer. Dev fields can appear on **any** message (in the sample files, session and laps carry one). Apply scale/offset when present, and filter sentinels exactly as for standard fields.
- **Conversions:** `physical = raw / scale − offset` (the Profile.xlsx convention; altitude uses scale 5, offset 500). Semicircles to degrees: `× 180 / 2**31`. FIT epoch to Unix: `+ 631_065_600`.

**Base types.** Look these up by `base_type & 0x1F`. Check sentinels on the raw value, before scaling.

| # | byte | type | struct | invalid | | # | byte | type | struct | invalid |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0x00 | enum | B | 0xFF | | 9 | 0x89 | float64 | d | all bits 1 |
| 1 | 0x01 | sint8 | b | 0x7F | | 10 | 0x0A | uint8z | B | **0** |
| 2 | 0x02 | uint8 | B | 0xFF | | 11 | 0x8B | uint16z | H | **0** |
| 3 | 0x83 | sint16 | h | 0x7FFF | | 12 | 0x8C | uint32z | I | **0** |
| 4 | 0x84 | uint16 | H | 0xFFFF | | 13 | 0x0D | byte | B | all 0xFF |
| 5 | 0x85 | sint32 | i | 0x7FFFFFFF | | 14 | 0x8E | sint64 | q | 0x7FFF…FF |
| 6 | 0x86 | uint32 | I | 0xFFFFFFFF | | 15 | 0x8F | uint64 | Q | 0xFFFF…FF |
| 7 | 0x07 | string | s | empty | | 16 | 0x90 | uint64z | Q | **0** |
| 8 | 0x88 | float32 | f | all bits 1 | | | | | | |

## Profile subset to start with

Format is `num name (scale, offset) unit`. ✓ means cross-checked against the sample files. Check anything new against Profile.xlsx.

- **record 20:** 253 timestamp ✓ · 0/1 position_lat/long (semicircles) ✓ · 2 altitude (5, 500) m ✓ · 3 heart_rate bpm ✓ · 4 cadence rpm (running: one foot, so steps/min = 2 ×) ✓ · 5 distance (100) m ✓ · 6 speed (1000) m/s ✓ · 7 power W ✓ · 29 accumulated_power W · 39 vertical_oscillation (10) mm ✓ · 41 stance_time (10) ms ✓ · 42 activity_type · 53 fractional_cadence (128) rpm · 73 enhanced_speed (1000) m/s · 78 enhanced_altitude (5, 500) m · **83 vertical_ratio (100) %** ✓ · **85 step_length (10) mm** ✓
- **session 18:** 253 timestamp · 2 start_time · 5 sport · 6 sub_sport · 7 total_elapsed_time (1000) s · 8 total_timer_time (1000) s · 9 total_distance (100) m · 10 total_cycles · 11 total_calories kcal · 14/15 avg/max_speed (1000) m/s · 16/17 avg/max_heart_rate · 18/19 avg/max_cadence · 20/21 avg/max_power · 22/23 total_ascent/descent m · 35 training_stress_score (10)
- **lap 19:** uses the same names as session but **different numbers**: 13/14 avg/max_speed · 15/16 avg/max_heart_rate · 17/18 avg/max_cadence · 19/20 avg/max_power · 21/22 total_ascent/descent · 23 intensity · 24 lap_trigger · 25 sport · 254 message_index. Fields 2, 7, 8, 9 and 11 match session. **So the profile must be keyed by (message, field), never by field number alone.**
- **event 21:** 0 event (0 = timer) · 1 event_type (0 start, 1 stop, 4 stop_all). Timer start/stop_all pairs give the exact pauses.
- **file_id 0:** 0 type · 1 manufacturer · 2 product · 3 serial_number (uint32z) · 4 time_created · 8 product_name
- **device_info 23:** 0 device_index · 2 manufacturer · 4 product · 5 software_version (100) · 27 product_name
- **activity 34:** 0 total_timer_time (1000) · 1 num_sessions · 5 local_timestamp
- **field_description 206:** 0 developer_data_index · 1 field_definition_number · 2 fit_base_type_id · 3 field_name · 6 scale · 7 offset · 8 units · 14 native_mesg_num · 15 native_field_num
- **developer_data_id 207:** 1 application_id · 2 manufacturer_id · 3 developer_data_index · 4 application_version
- **Enums** (subset; an unknown value becomes the string `"<enum>_<n>"`):
  - manufacturer: 1 garmin, 23 suunto, 32 wahoo_fitness, 95 stryd, 123 polar_electro, 255 development, 294 coros
  - sport: 0 generic, 1 running, 2 cycling, 5 swimming, 10 training, 11 walking, 17 hiking
  - sub_sport: 0 generic, 1 treadmill, 2 street, 3 trail, 4 track
- **`VENDOR_FIELDS`** are applied only when `file_id.manufacturer` matches. Example: Coros (294) session field 91 is a TSS/training-load value (÷10, name unverified). Vendor quirks live here and never in the decoder.

## Analysis layer (pandas/numpy)

- **Smoother contract:** `Callable[[pd.Series], pd.Series]` that keeps the same index and length, never drops rows, and returns NaN wherever the input was NaN. Configure smoothers with `functools.partial`. To compare variants, build a dict `{label: smoother}` and run each over **one** parsed DataFrame.
- **Windows are time-based** (`"10s"`) on the DatetimeIndex, because records have gaps (pauses of up to 139 s in long_run). `sma` is `rolling(w, center=True, min_periods=1).mean()`. `ema` is `ewm(alpha=a, adjust=False).mean()`, which matches the Rust EMA (seeded with the first value).
- Smooth **per segment** so that no state carries across a stop: `s.groupby(segment_id).transform(f)`.
- `moving_segments(df, min_speed=1.0, min_stop="5s")` returns a segment id per row (NaN while stopped). A segment ends on any of:
  - speed below the threshold for at least `min_stop`
  - a timer stop/start event
  - a time gap

  Records with no speed count as "unknown"; they are not silently dropped.
- **Intervals:** use laps first (`laps_df()` classified by average speed/HR). Manual `ginput` clicking is only a fallback for unstructured runs.
- **HR zones:** zone functions take the zone boundaries as an argument. Scripts and notebooks load them from `hr_zones.toml` with `tomllib`, and the package never reads the TOML itself. Time in zone is the sum of `Δt`, not a row count.
- Personal constants (max HR, zones) live in TOML. Experiment parameters are keyword arguments (`--flags` in scripts). No global config, and no positional CLI arguments except the input file.

## Testing

- `tests/fitwriter.py` is a minimal FIT *encoder*: header, definitions, data, dev fields, compressed headers, big-endian, CRC. Every reader feature gets a synthetic test. The sample files contain **no compressed timestamps** and are all little-endian, so those code paths are untested otherwise.
- Golden tests use `data/raw/*.fit` (skipped if absent). The values below were verified with an independent decoder.
- Port the Rust smoother tests: a flat signal stays unchanged, length and index are preserved, and `ema(alpha=1)` is the identity.
- Optional oracle: Garmin's official `garmin-fit-sdk` in the dev group (`uv add --dev garmin-fit-sdk`), imported only by tests to cross-check decoded values and profile scales. It checks our decoder and never replaces it, so `src/` never imports it.

## Sample-file facts

All three files come from a Coros PACE Pro, are little-endian, have valid CRCs and contain 0 compressed headers.

| file | bytes | record | lap | event | session | dev fields |
|---|---|---|---|---|---|---|
| long_run.fit | 533,216 | 9,825 | 31 | 10 | 1 | Form Power, Leg Spring Stiffness, Air Power, Impact Loading Rate (sint32), Effort Pace |
| intervals_example.fit | 137,457 | 3,237 | 15 | 6 | 1 | Effort Pace only (no Stryd fields) |
| treadmill.fit | 129,644 | 3,558 | 18 | 4 | 1 | Form Power, Leg Spring Stiffness, Impact Loading Rate; no GPS, no step_length; sub_sport 45 |

- **long_run session:** total_distance 30385.53 m, total_timer_time 9823.74 s, total_elapsed_time 10151.56 s, avg/max HR 143/162, avg/max speed 3.093/3.846, calories 2330, ascent/descent 218/226, avg_power 252, avg_cadence 91. Manufacturer is 294, product_name is "COROS PACE Pro". There are 4 record gaps longer than 1 s (max 139 s), matching the timer stop/start events.
- **Physics checks:** `vertical_ratio ≈ 100 · vertical_oscillation / step_length` (median difference 0.1 percentage points). `step_length ≈ speed / (2 · cadence / 60)` (1020 vs 1022 mm).
- **intervals_example laps:** laps 4, 6, 8, 10 and 12 are the 1000 m reps (~4.2 m/s); laps 5, 7, 9 and 11 are the ~150 s rests.

## Lessons from the Rust version (don't reintroduce)

- **Forward parsing works.** A correct single pass reaches the last byte of all three sample files, and they contain only standard messages (0, 18, 19, 20, 21, 23, 34, 206, 207). Two things in the Rust version were workarounds for an old parser bug, not for the files: the "Coros proprietary messages break sync" theory, and the backwards session pattern search. The documented failure at byte 6,454 is inside an ordinary record.
- A fixed 16-channel struct dropped data (Effort Pace, accumulated_power, laps, events) and needed a code change for every new field. Use generic dicts instead.
- The scale table was keyed by field number alone. It must be keyed by message and field.
- The base-type table was wrong: the z-types' invalid value is 0, 0x8B/0x8C are uint16z/uint32z, and floats ignored the endianness flag. Use the table above.
- Epochs were mixed: records used Unix time, metadata used the FIT epoch, and the docs contradicted both. Use one convention.
- Smoothing windows were point-based, and missing values were compacted out before smoothing. Use time-based, NaN-preserving windows.
- Parsing, smoothing and the CSV format were coupled behind a `cargo run` subprocess that ran once per variant. Parse once, then experiment in-process.
- Hand-written enum tables had wrong codes: Coros was listed as 263, but the files say 294. Copy codes from Profile.xlsx.

## Open questions (log outcomes in NOTES.md)

- **Stop detection:** speed threshold, timer events, or both? How long a debounce?
- Should 0 be treated as missing in running dynamics (stance_time, vertical_oscillation, ...)?
- **Smoother comparison at equal effective lag:** SMA vs EMA vs Savitzky–Golay (`scipy.signal.savgol_filter`) vs Kalman.
- Are laps enough to define intervals, or do structured workouts also need `workout_step` (27)?
- **Is a compiled core still needed?** A naive pure-Python walk of long_run.fit takes about 0.12 s. Revisit PyO3/Rust only if a full decode of the largest file exceeds about 1 s.
