import pandas as pd

import build_data
import make_fake_data
from pipeline import calibrate


def test_build_data_is_calibrate_on_renamed_columns(tmp_path):
    make_fake_data.main(tmp_path)  # fake cells with cy / cx
    cells = pd.read_parquet(tmp_path / "cells.parquet")
    theirs = tmp_path / "ingest_cells.parquet"  # the column names harmonization-modis-viirs writes
    cells.rename(columns={"cy": "lat_i", "cx": "lon_i"}).to_parquet(theirs, index=False)

    build_data.main(theirs, tmp_path / "built.parquet")
    calibrate.main(str(tmp_path / "cells.parquet"), str(tmp_path / "direct.parquet"))
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "built.parquet"),
                                  pd.read_parquet(tmp_path / "direct.parquet"))
    assert ((tmp_path / "built_validation.json").read_text()
            == (tmp_path / "direct_validation.json").read_text())


def test_export_fires_keeps_vegetation_detections_inside_the_grid(tmp_path):
    import export_fires
    rows = pd.DataFrame([  # synthetic FIRMS-like rows
        dict(latitude=36.8, longitude=10.2, type=0, confidence="n"),   # kept
        dict(latitude=37.2, longitude=9.9, type=0, confidence="h"),    # kept
        dict(latitude=36.8, longitude=10.2, type=2, confidence="h"),   # not vegetation
        dict(latitude=36.8, longitude=10.2, type=0, confidence="l"),   # low confidence
        dict(latitude=37.5, longitude=10.0, type=0, confidence="n"),   # north edge: outside
        dict(latitude=31.5, longitude=5.8, type=0, confidence="h"),    # Saharan gas field
    ]).assign(acq_date="2017-08-04", acq_time=1230, satellite="N", frp=3.2)
    (tmp_path / "raw" / "viirs_snpp").mkdir(parents=True)
    rows.to_csv(tmp_path / "raw" / "viirs_snpp" / "viirs-snpp_2017_Tunisia.csv", index=False)
    assert export_fires.main(tmp_path / "raw", tmp_path / "fires.csv") == 2
    out = pd.read_csv(tmp_path / "fires.csv")
    assert list(out.columns) == export_fires.COLUMNS
