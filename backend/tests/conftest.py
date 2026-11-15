"""Synthetic test data. NOT real FIRMS data: shapes match pipeline/calibrate.py output, values are made up."""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

# Two cells inside the "bizerte" area, one inside "kabylie" (cell = floor(deg / 0.5)).
CELLS = [(73, 18), (74, 19), (72, 8)]
YEARS = range(2008, 2016)  # straddles the 2012 MODIS -> VIIRS switch
VALIDATION = {"train_years": [2012, 2019], "test_years": [2020, 2021],
              "error_pct_uncorrected": 66.4, "error_pct_global_ratio": 23.1,
              "error_pct_cell_month_ratio": 20.9, "k_global": 3.04, "test_months": 24}


def make_dataset(folder, optional_cols=False, series=None) -> str:
    """Write harmonized.parquet + harmonized_validation.json into folder; return the parquet path."""
    rng = np.random.default_rng(0)
    rows = []
    for cy, cx in CELLS:
        for month in range(1, 13):
            lam = 2 + 40 * np.exp(-((month - 8) ** 2) / 4)  # summer peak
            k, support = 3.0, (10 if month == 1 else 200)    # January ratio rests on little data
            for year in YEARS:
                modis = int(rng.poisson(lam * (8 if (year, month) == (2014, 3) else 1)))
                viirs = int(rng.poisson(3 * lam)) if year >= 2012 else 0
                if modis == 0 and viirs == 0:
                    continue  # calibrate.py only writes cell-months with detections
                rows.append(dict(cy=cy, cx=cx, year=year, month=month, MODIS=modis, VIIRS=viirs,
                                 k=k, k_month=k, support=support,
                                 harmonized=modis * k if year < 2012 else viirs,
                                 source="MODIS*k" if year < 2012 else "VIIRS"))
    df = pd.DataFrame(rows)
    if optional_cols:  # bounds only where the value is MODIS * k (pre-2012), NaN afterwards
        pre = df.year < 2012
        df["low"], df["high"] = (df.harmonized * 0.8).where(pre), (df.harmonized * 1.2).where(pre)
        df["MODIS_days"], df["VIIRS_days"] = np.minimum(df.MODIS, 28), np.minimum(df.VIIRS, 28)
    path = folder / "harmonized.parquet"
    df.to_parquet(path, index=False)
    report = dict(VALIDATION, **({"series": series} if series is not None else {}))
    (folder / "harmonized_validation.json").write_text(json.dumps(report))
    return str(path)


@pytest.fixture(autouse=True)
def api_without_database(monkeypatch):
    """The API under test never sees the developer's real DATABASE_URL (or a cached engine/frame).

    API tests that need PostgreSQL set DATABASE_URL to their own TEST_DATABASE_URL schema."""
    from api import core, database
    for var in ("DATABASE_URL", "REPORTS_ADMIN_TOKEN", "API_VERSION_CHECK_S", "API_DB_CONNECT_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    database.reset()
    core.reset_cache()
    yield
    database.reset()
    core.reset_cache()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", make_dataset(tmp_path))
    from api.main import app
    return TestClient(app)
