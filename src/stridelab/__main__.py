"""python -m stridelab FILE: a one-screen summary of a .fit file."""

import argparse
from datetime import datetime

from .activity import read
from .reader import FitParseError


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m stridelab", description=__doc__)
    parser.add_argument("file", help="path to a .fit file")
    args = parser.parse_args(argv)
    try:
        summary = read(args.file).summary()
    except FitParseError as e:
        parser.exit(1, f"error: {e}\n")
    warnings = summary.pop("warnings")
    width = max(map(len, summary))
    for key, value in summary.items():
        print(f"{key:<{width}}  {_format(value)}")
    for warning in warnings:
        print(f"warning: {warning}")


def _format(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.10g}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "-"
    return str(value)


if __name__ == "__main__":
    main()
