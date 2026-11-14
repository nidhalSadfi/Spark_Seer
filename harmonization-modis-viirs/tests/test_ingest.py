from pathlib import Path

import pandas as pd

import zipfile

from pipeline.ingest import aggregate, main, parse_min_lat, prepare, sensor_from_path
from pipeline.weather import centre


def _write(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_filters_and_distinct_days(tmp_path: Path):
    raw = tmp_path / "raw"
    modis = raw / "modis_c61"
    viirs = raw / "viirs_snpp"
    modis.mkdir(parents=True)
    viirs.mkdir()
    _write(
        modis / "modis_2019_Tunisia.csv",
        [
            # kept: two detections, one day, cell south-west corner 36.0, 9.0
            dict(latitude=36.1, longitude=9.2, acq_date="2019-08-01", confidence=80, type=0, version="6.03"),
            dict(latitude=36.4, longitude=9.4, acq_date="2019-08-01", confidence=30, type=0, version="6.03"),
            # low confidence, south of 34, not vegetation
            dict(latitude=36.2, longitude=9.2, acq_date="2019-08-02", confidence=29, type=0, version="6.03"),
            dict(latitude=33.9, longitude=9.2, acq_date="2019-08-02", confidence=90, type=0, version="6.03"),
            dict(latitude=36.2, longitude=9.2, acq_date="2019-08-03", confidence=90, type=2, version="6.03"),
        ],
    )
    _write(
        viirs / "viirs-snpp_2019_Algeria.csv",
        [
            dict(latitude=36.6, longitude=4.1, acq_date="2019-07-10", confidence="n", type=0, version="2"),
            dict(latitude=36.6, longitude=4.2, acq_date="2019-07-11", confidence="h", type=0, version="2"),
            dict(latitude=36.6, longitude=4.2, acq_date="2019-07-11", confidence="l", type=0, version="2"),
            dict(latitude=31.6, longitude=5.8, acq_date="2019-07-11", confidence="h", type=0, version="2"),
        ],
    )
    out = tmp_path / "cells.parquet"
    main([str(raw), str(out), "34"])
    cells = pd.read_parquet(out)
    modis_rows = cells[cells.sensor == "MODIS"]
    viirs_rows = cells[cells.sensor == "VIIRS"]
    assert len(modis_rows) == 1
    assert int(modis_rows.iloc[0].n) == 2
    assert int(modis_rows.iloc[0].days) == 1
    assert modis_rows.iloc[0].cell_id == "36.0_9.0"
    assert int(modis_rows.iloc[0].year) == 2019
    assert int(modis_rows.iloc[0].month) == 8
    assert abs(modis_rows.iloc[0].lat - 36.25) < 1e-6
    assert int(viirs_rows.n.sum()) == 2
    assert int(viirs_rows.days.sum()) == 2
    assert set(viirs_rows.cell_id) == {"36.5_4.0"}


def test_cell_edges_and_negative_longitude():
    frame = pd.DataFrame(
        {
            "latitude": [36.0, 36.4999, 35.9999, 36.5],
            "longitude": [0.0, -0.01, 8.5, -2.0],
            "acq_date": ["2012-01-20"] * 4,
            "sensor": ["VIIRS"] * 4,
        }
    )
    cells = aggregate(frame)
    assert set(cells.cell_id) == {"36.0_0.0", "36.0_-0.5", "35.5_8.5", "36.5_-2.0"}


def test_cell_id_south_and_west_of_the_origin():
    frame = pd.DataFrame(
        {
            "latitude": [-33.2, -33.4, -0.5, -90.0, 0.0],
            "longitude": [-71.2, -71.4, -0.5, -180.0, 0.0],
            "acq_date": ["2019-01-15"] * 5,
            "sensor": ["MODIS"] * 5,
        }
    )
    cells = aggregate(frame)
    southwest = cells[cells.cell_id == "-33.5_-71.5"]
    assert len(southwest) == 1
    assert int(southwest.iloc[0].n) == 2
    assert abs(float(southwest.iloc[0].lat0) + 33.5) < 1e-9
    assert abs(float(southwest.iloc[0].lon0) + 71.5) < 1e-9
    assert set(cells.cell_id) == {"-33.5_-71.5", "-0.5_-0.5", "-90.0_-180.0", "0.0_0.0"}
    lat_s, lon_s = "-33.5_-71.5".split("_")
    assert (lat_s, lon_s) == ("-33.5", "-71.5")


def test_viirs_letters_and_sensor_paths():
    kept, stats = prepare(
        pd.DataFrame(
            {
                "latitude": [36.0, 36.0],
                "longitude": [8.0, 8.0],
                "acq_date": ["2012-06-01", "2012-06-01"],
                "confidence": ["N", "L"],
                "type": [0, 0],
                "version": ["2", "2"],
            }
        ),
        "VIIRS",
        34,
    )
    assert stats["kept"] == 1
    assert len(kept) == 1
    assert sensor_from_path(Path("data/raw/viirs_snpp/viirs-snpp_2012_Tunisia.csv")) == "VIIRS"
    assert sensor_from_path(Path("data/raw/modis_c61/modis_2001_Algeria.csv")) == "MODIS"
    assert sensor_from_path(Path("data/raw/viirs_noaa20/viirs-jpss1_2020_Tunisia.csv")) is None


def test_area_centre():
    assert centre([8.3, 36.0, 9.8, 37.2]) == (36.6, 9.05)


def test_latitude_floor_tokens(capsys):
    assert parse_min_lat("34") == 34.0
    assert parse_min_lat("0") == 0.0
    assert parse_min_lat(None) is None
    assert parse_min_lat("none") is None
    assert parse_min_lat("WORLD") is None
    main(["--help"])
    text = capsys.readouterr().out
    assert "python pipeline/ingest.py data/raw data/cells.parquet\n" in text
    assert "data/cells.parquet 34" in text
    assert "none" in text
    assert "world grid" in text


def test_no_floor_still_drops_low_confidence():
    kept, stats = prepare(
        pd.DataFrame(
            {
                "latitude": [-10.0, -10.0],
                "longitude": [20.0, 20.0],
                "acq_date": ["2012-06-01", "2012-06-01"],
                "confidence": ["h", "l"],
                "type": [0, 0],
                "version": ["2", "2"],
            }
        ),
        "VIIRS",
        None,
    )
    assert stats["north"] == 2
    assert stats["kept"] == 1
    assert len(kept) == 1


def test_global_keeps_southern_rows_and_zip_members(tmp_path: Path, capsys):
    raw = tmp_path / "raw" / "modis_c61"
    raw.mkdir(parents=True)
    (raw / "modis_2019_Tunisia.csv").write_text(
        "latitude,longitude,acq_date,confidence,type,version\n"
        "36.1,9.2,2019-08-01,80,0,6.03\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(raw / "modis_2019_all_countries.zip", "w") as archive:
        archive.writestr(
            "modis_2019_Brazil.csv",
            "latitude,longitude,acq_date,confidence,type,version\n"
            "-33.2,150.2,2019-01-15,80,0,6.03\n"
            "33.9,9.2,2019-08-02,90,0,6.03\n"
            "-33.2,150.2,2019-01-15,10,0,6.03\n"
            "-10.0,20.0,2019-01-15,90,2,6.03\n",
        )
        archive.writestr(
            "viirs-jpss1_2019_Brazil.csv",
            "latitude,longitude,acq_date,confidence,type,version\n"
            "1.0,1.0,2019-01-01,h,0,2\n",
        )
    root = tmp_path / "raw"
    north_out = tmp_path / "north.parquet"
    main([str(root), str(north_out), "34"])
    north = pd.read_parquet(north_out)
    assert set(north.cell_id) == {"36.0_9.0"}
    assert int(north.n.sum()) == 1

    global_out = tmp_path / "global.parquet"
    main([str(root), str(global_out), "none"])
    cells = pd.read_parquet(global_out)
    assert set(cells.cell_id) == {"36.0_9.0", "33.5_9.0", "-33.5_150.0"}
    assert int(cells.n.sum()) == 3

    omitted = tmp_path / "omitted.parquet"
    main([str(root), str(omitted)])
    assert set(pd.read_parquet(omitted).cell_id) == set(cells.cell_id)
    assert "viirs-jpss1_2019_Brazil.csv" in capsys.readouterr().out
