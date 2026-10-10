# Stridelab

Explore running data from `.fit` files. Stridelab has its own pure-Python FIT decoder, which reads standard fields and developer fields such as Stryd's Form Power. Analysis happens in [marimo](https://marimo.io) notebooks with pandas or polars: smoothing, moving/stopped segments, intervals and HR zones. The goal is to find out which analyses are worth keeping.

It replaces FitTrackz, the earlier Rust attempt. The design rules, FIT format notes and lessons learned are in [CLAUDE.md](CLAUDE.md).

> **Status:** the decoder and the `Activity` API work (slice 01, see [docs/slice01.md](docs/slice01.md)). Smoothing and segmentation come next; the layout below still lists some files that don't exist yet.

## Quick start

```bash
uv sync                                          # creates .venv and installs everything, incl. the package itself
mkdir -p data/raw data/derived
cp ../FitTrackz/data/raw/*.fit data/raw/         # sample files are not in git
uv run python -m stridelab data/raw/long_run.fit # one-screen summary
uv run marimo edit notebooks/first_look.py       # summary, dev fields, channel plots, a first polars cell
```

```python
import stridelab as sl

act = sl.read("data/raw/long_run.fit")   # ~50 ms for a 3-hour run
act.summary()                            # device, sport, start, distance_m, timer_time_s, avg HR, ...
df = act.records_df()                    # pandas: UTC DatetimeIndex, form_power, step_length, ..., elapsed_s
```

## Layout

```
Stridelab/
├── src/stridelab/            the package: FIT decoder + reusable analysis
│   ├── reader.py             bytes → messages (our own decoder, stdlib only)
│   ├── profile.py            FIT names, scales, units, enums (from the SDK's Profile.xlsx)
│   ├── activity.py           Activity: session, laps, records, records_df(), laps_df()
│   ├── smoothing.py          Series → Series smoothers (sma, ema, …)
│   ├── segments.py           moving/stopped segments, laps → intervals, HR zones
│   └── __main__.py           python -m stridelab FILE [--scan | --csv]
├── tests/                    pytest: synthetic FIT writer, golden tests on data/raw/, SDK cross-check
├── notebooks/                marimo notebooks, one question each, e.g.
│   ├── first_look.py         summary, dev fields and the main channels of one run
│   ├── smoother_comparison.py
│   ├── intervals.py          reps from laps: pace, HR recovery, dynamics per rep
│   └── pandas_vs_polars.py   the same analysis in both, to learn polars
├── scripts/                  repeatable, non-interactive jobs, e.g.
│   ├── export.py             write records/laps to data/derived/
│   ├── bench_decode.py       decode timing (is a compiled core ever needed?)
│   └── mark_intervals.py     click-to-mark intervals (matplotlib ginput) for unstructured runs
├── data/
│   ├── raw/                  .fit files exactly as they came off the watch (not in git)
│   └── derived/              generated csv / parquet / png (not in git, safe to delete)
├── hr_zones.toml             personal max HR and zone boundaries, for scripts and notebooks
├── NOTES.md                  dated experiment log: tried → result → decision
├── CLAUDE.md                 design rules and FIT format reference
└── pyproject.toml, uv.lock
```

### Where does code go?

| You want to… | Put it in |
|---|---|
| look at a run, try parameters, plot things | `notebooks/` |
| produce the same output again, or for many files | `scripts/` |
| reuse a function you've already used in two places and can test | `src/stridelab/` |
| record what you tried and what you decided | `NOTES.md` |

Code usually starts in a notebook, sometimes becomes a script, and moves into the package once it has been reused and has a test. Notebooks and scripts import `stridelab` but never import each other.

`data/raw/` is read-only input. Anything a notebook or script writes goes to `data/derived/`, so that folder can always be deleted and regenerated.

## Notebooks

marimo notebooks are plain `.py` files. That means they diff cleanly in git, run top to bottom with `uv run python notebooks/x.py`, and open as a read-only app with `uv run marimo run notebooks/x.py`.

```python
import marimo as mo
import polars as pl
import stridelab as sl

act = sl.read(mo.notebook_dir().parent / "data" / "raw" / "long_run.fit")

df = act.records_df()                                     # pandas: UTC DatetimeIndex, + elapsed_s, units in df.attrs
pf = pl.DataFrame(act.records, infer_schema_length=None)  # polars: straight from the decoded records
```

`records_df()` also fills `speed`/`altitude` from their enhanced variants and adds `elapsed_s`. In polars you derive those yourself, which is good practice. Keep `infer_schema_length=None`: without it, polars fails on any column that is empty for the first 100 rows.

marimo quirks: a variable can be defined in only one cell (prefix throwaway names with `_`), and a cell shows its last expression, so end plotting cells with the figure. Always use the project environment, not `--sandbox`.

## Scripts

Each script uses argparse. The input file is the only positional argument, every parameter is a `--flag`, and output goes to `data/derived/`:

```bash
uv run python scripts/bench_decode.py data/raw/long_run.fit --repeat 5
```

## Development

```bash
uv run pytest -q                                   # golden tests skip if data/raw/ is empty
uv run ruff check --fix . && uv run ruff format .
uv add <pkg>                                       # dependency the package needs
uv add --dev <pkg>                                 # notebooks, scripts, tests only
```

The package depends only on numpy, pandas and matplotlib. polars, marimo, pytest, ruff and garmin-fit-sdk are dev dependencies and are never imported from `src/`. FIT decoding is always our own code: no `fitparse`, `fitdecode` or Garmin SDK at runtime. The Garmin SDK is only an oracle in `tests/test_oracle.py`, which checks our profile tables and every decoded value against it.

## License

MIT, see [LICENSE](LICENSE).
