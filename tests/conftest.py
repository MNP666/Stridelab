from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="session")
def fit_file():
    """Return a function name -> data/raw/<name>.fit that skips the test if the file is missing."""

    def get(name: str) -> Path:
        path = DATA / f"{name}.fit"
        if not path.exists():
            pytest.skip(f"{path.name} is not in data/raw/")
        return path

    return get
