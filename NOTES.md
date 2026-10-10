# Notes

Dated experiment log: what was tried → what happened → what was decided. Newest first.

## 2026-10-10: Slice 01, M2–M6 (profile, dev fields, Activity API, demo, hardening)

**Tried:** profile decoding generated from Garmin's `garmin-fit-sdk` (dev dependency only), cross-checked value by value against the SDK's own decoder on all three files (`tests/test_oracle.py`).

**Result:**
- **Every value we decode matches the SDK**, apart from four places where we deliberately follow CLAUDE.md and the SDK doesn't. The test handles each one explicitly:
  - The SDK keeps raw sentinels in developer fields (65535, 2147483647). We turn them into `None`.
  - It leaves `activity.local_timestamp` as a raw int; we convert it to a datetime.
  - It leaves `developer_data_id.manufacturer_id` as a code (294); we use `coros`.
  - Coros writes `event.data` (uint32) in **1 byte**. Since the size isn't a multiple of the base type, we keep it as bytes; the SDK reads the byte as an int.
- **Coros session field 91 is not a training load.** The SDK profile says session field 91 is `avg_stance_time` (scale 10, ms). The values agree with the records: 236.0 vs a mean record stance_time of 236.3 ms on long_run, 214.0 vs 213.2, and 235.0 vs 235.0. The Rust version's "training_stress_score: 236.0" was a stance time with a guessed meaning, which is exactly what rule 6 warns against. `VENDOR_FIELDS` keeps the mechanism (tested) but has no entries.
- **Timing (long_run, 533 kB):** `sl.read()` takes **54 ms** and `records_df()` 17 ms. The Garmin SDK takes 0.33 s for the same file. A compiled core is not needed (open question answered).
- **Other file facts:**
  - treadmill's sub_sport 45 is `indoor_running`.
  - Each file has a single `device_info` without `device_index`.
  - Coros's `activity.total_timer_time` holds the *elapsed* time (10151.56 s, not 9823.74 s).
  - The local time is UTC+1 in all three files.
  - The dev-field descriptions have no scale or offset.
  - Effort Pace is described twice in long_run and intervals_example.
- **Zeros after every pause:** in the first record after each timer restart, speed, cadence, step_length and form_power are exactly 0. This bears on the open question "is 0 missing in running dynamics?". The stop detector in the next slice should handle these records.

**Decisions:**
- **Profile:** the CLAUDE.md subset plus every field seen in the sample files, generated from the SDK and checked by `tests/test_oracle.py`. Enums are complete except manufacturer, sport and sub_sport, which are subsets. Add more from the SDK as needed.
- **Semicircles → degrees in the reader**, with units `deg`, like `date_time` → datetime. `Activity.units` reports the decoded units (`reader.output_units`).
- **Arrays:** `None` per invalid element, and `None` for the whole field when every element is invalid.
- **`local_date_time`:** a datetime labelled UTC that is really wall-clock time, so `local_timestamp - timestamp` gives the UTC offset (`summary()["utc_offset"]`).
- **Raw mode keeps dev fields as bytes** (`dev_<index>_<num>`), so the walk can be debugged without any description being applied.
- **`VENDOR_FIELDS` is keyed by manufacturer name** (`("coros", 18)`), matching the decoded `file_id.manufacturer`.
- **`Activity.select(num)`** returns any message type as dicts. `records`, `laps` and `events` are `select(20/19/21)`, so they also work in raw mode. `summary()` and `*_df()` raise in raw mode.
- **Truncated files are recoverable:** a warning, plus every complete message before the break. A file missing only its CRC also gets a warning.
- **Ruff:** marimo cells break B018 and PLR1711 by design (a bare last expression, a trailing `return`), so those two rules are ignored for `notebooks/`.

## 2026-10-10: Slice 01, M0–M1 (skeleton and raw message walk)

**Tried:** a single forward pass over all three sample files in raw mode (`iter_messages(path, raw=True)`), with one `struct.Struct` compiled per definition.

**Result:**
- All three files walk to exactly the last data byte. Header and file CRCs are valid, and there are no warnings. Message counts match CLAUDE.md.
- Timing on long_run.fit (533 kB, 9877 messages): about **31 ms** for the whole raw walk, of which the CRC is about 20 ms. intervals_example and treadmill take about 10 ms each. CLAUDE.md's estimate was 0.12 s. A compiled core is clearly not needed for decoding. Profile decoding (M2) will add some time; measure again then.
- Dev fields appear in definitions in all three files, so the walk can't treat them as unsupported (the first version of the plan said it could). The raw keys match CLAUDE.md's table:
  - long_run records: `dev_0_8, dev_0_9, dev_0_11, dev_0_16, dev_0_24` (5 Stryd/Coros fields)
  - intervals_example records: `dev_0_16` only (Effort Pace)
  - treadmill records: `dev_0_8, dev_0_9, dev_0_24`
  - session and laps carry `dev_0_16` (not on treadmill)
- Raw long_run session `field_9` (total_distance) is 3038553, which is 30385.53 m at scale 100, as in CLAUDE.md.

**Decisions:**
- **CRC:** FIT's CRC is CRC-16/ARC. The reader uses a 256-entry table. `tests/fitwriter.py` uses the SDK's nibble loop, so the two cross-check each other.
- **Warnings:** `iter_messages(..., warnings=some_list)` appends to the list. Without a list they go through `warnings.warn`, so they're never silent. `Activity` (M4) will pass its own list.
- **Dev fields before M3:** kept as raw bytes in `Message.dev_fields` under `dev_<index>_<num>`. Their sizes come from the definition, so the walk stays in sync without the field descriptions.
- **Unknown base type:** kept as raw bytes plus a warning. The size is known, so nothing is dropped and the walk stays in sync.
- **Strings and bytes:** the `byte` base type stays `bytes`. Strings are decoded as UTF-8 up to the first NUL; undecodable ones stay `bytes`. Raw mode keeps `""` (the string sentinel), and M2 maps it to `None`.
- **Not supported yet, so these raise `FitParseError`:** compressed timestamp headers (M6) and truncated files (M6 turns this into a warning). `raw=False` raises `NotImplementedError` until M2.
- **Tooling:** ruff line length is 100, and Markdown is excluded from `ruff format`. Without that, it reflows the hand-aligned code blocks in the docs.
- **`hr_zones.toml`** is input for scripts and notebooks. Zone functions in the package will take zones as an argument.
