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
