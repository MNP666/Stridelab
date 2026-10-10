# Notes

Dated experiment log: what was tried → what happened → what was decided. Newest first.

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
