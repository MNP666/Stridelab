import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import dataclasses

    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd
    import polars as pl

    import stridelab as sl

    return dataclasses, mo, pd, pl, plt, sl


@app.cell
def _(mo):
    mo.md("""
    # First look

    One activity decoded by stridelab: the summary, the developer fields, and the main
    channels against elapsed time. At the end, the same records in polars.
    """)
    return


@app.cell
def _(mo):
    data_dir = mo.notebook_dir().parent / "data" / "raw"
    _names = sorted(p.name for p in data_dir.glob("*.fit"))
    _default = "long_run.fit" if "long_run.fit" in _names else next(iter(_names), None)
    file = mo.ui.dropdown(_names, value=_default, label="Activity")
    file
    return data_dir, file


@app.cell
def _(data_dir, file, mo, sl):
    mo.stop(file.value is None, mo.md(f"No .fit files in `{data_dir}`. Copy some into data/raw/."))
    act = sl.read(data_dir / file.value)
    act.summary()
    return (act,)


@app.cell
def _(act, dataclasses, pd):
    pd.DataFrame([dataclasses.asdict(d) for d in act.dev_fields])
    return


@app.cell
def _(act, plt):
    df = act.records_df()
    _units = df.attrs["units"]
    _channels = [
        c
        for c in ("heart_rate", "speed", "cadence", "step_length", "form_power")
        if c in df and df[c].notna().any()
    ]
    _fig, _axes = plt.subplots(
        len(_channels), 1, figsize=(10, 1.8 * len(_channels)), sharex=True, squeeze=False
    )
    for _ax, _channel in zip(_axes[:, 0], _channels):
        _ax.plot(df["elapsed_s"] / 60, df[_channel], lw=0.7)
        _ax.set_ylabel(f"{_channel}\n({_units.get(_channel, '')})", fontsize=8)
        _ax.grid(alpha=0.3)
    _axes[-1, 0].set_xlabel("elapsed (min, pauses included)")
    _fig.tight_layout()
    _fig
    return (df,)


@app.cell
def _(mo):
    mo.md("""
    ## The same records in polars

    `pl.DataFrame(act.records, infer_schema_length=None)` builds the frame straight from the
    decoded dicts. Polars has no index, so the timestamp stays a column, and derived columns
    such as `elapsed_s` are yours to add.

    **Exercise:** some devices only write `enhanced_speed`. Add a `speed_m_per_s` column that
    prefers `enhanced_speed` and falls back to `speed` (`pl.coalesce`), the way `records_df()`
    does it in pandas.
    """)
    return


@app.cell
def _(act, pl):
    pf = pl.DataFrame(act.records, infer_schema_length=None).with_columns(
        elapsed_s=(pl.col("timestamp") - pl.col("timestamp").first()).dt.total_seconds()
    )
    pf.select("timestamp", "elapsed_s", "heart_rate", "speed", "cadence")
    return (pf,)


@app.cell
def _(pf, pl, plt):
    _per_minute = (
        pf.with_columns(minute=(pl.col("elapsed_s") // 60))
        .group_by("minute")
        .agg(pl.col("heart_rate").mean())
        .sort("minute")
    )
    _fig, _ax = plt.subplots(figsize=(10, 2.2))
    _ax.plot(pf["elapsed_s"] / 60, pf["heart_rate"], lw=0.5, alpha=0.5, label="per record")
    _ax.plot(_per_minute["minute"], _per_minute["heart_rate"], lw=1.5, label="per-minute mean")
    _ax.set_xlabel("elapsed (min)")
    _ax.set_ylabel("heart_rate (bpm)")
    _ax.legend(fontsize=8)
    _ax.grid(alpha=0.3)
    _fig
    return


if __name__ == "__main__":
    app.run()
