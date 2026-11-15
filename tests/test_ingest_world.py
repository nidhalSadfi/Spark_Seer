"""scripts/ingest_world.py must write exactly what harmonization-modis-viirs/pipeline/ingest.py writes.

Synthetic FIRMS-like rows (not real data). The traps: one cell split across two country files on the
same day (days must count that day once), zipped files, and CSV chunks smaller than a file.
"""
import zipfile

import pandas as pd

import ingest_world


def row(lat, lon, date, conf, typ=0):
    return dict(latitude=lat, longitude=lon, acq_date=date, confidence=conf, type=typ, version="x")


def write_raw(raw):
    modis = {"Tunisia": [row(36.9, 8.9, "2008-07-01", 80), row(36.9, 8.9, "2008-07-02", 20),   # low conf
                         row(36.7, 8.6, "2008-07-01", 50), row(31.5, 5.8, "2008-07-01", 90)],
             "Algeria": [row(36.8, 8.55, "2008-07-01", 70), row(36.8, 8.55, "2008-08-03", 70, typ=2)]}
    viirs = {"Tunisia": [row(37.1, 9.8, "2015-08-04", "n"), row(37.1, 9.8, "2015-08-04", "h"),
                         row(37.1, 9.8, "2015-08-05", "l"), row(-33.6, -71.6, "2015-08-04", "n")],
             "Algeria": [row(37.1, 9.9, "2015-08-04", "h"), row(37.1, 9.9, "2015-08-06", "n")]}
    (raw / "modis_c61").mkdir(parents=True)
    for country, rows in modis.items():
        pd.DataFrame(rows).to_csv(raw / "modis_c61" / f"modis_2008_{country}.csv", index=False)
    (raw / "viirs_snpp").mkdir()
    with zipfile.ZipFile(raw / "viirs_snpp" / "viirs-snpp_2015_all_countries.zip", "w") as z:
        for country, rows in viirs.items():
            z.writestr(f"viirs-snpp/2015/viirs-snpp_2015_{country}.csv", pd.DataFrame(rows).to_csv(index=False))


def test_same_output_as_their_ingest(tmp_path, monkeypatch):
    write_raw(tmp_path / "raw")
    for min_lat in (None, 34.0):
        theirs = ingest_world.ingest.run(tmp_path / "raw", tmp_path / "theirs.parquet", min_lat)
        monkeypatch.setattr(ingest_world, "CHUNK", 2)  # several chunks per file
        ours = ingest_world.run(tmp_path / "raw", tmp_path / "ours.parquet", min_lat, workers=1)
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "ours.parquet"),
                                      pd.read_parquet(tmp_path / "theirs.parquet"))
        assert len(ours) == len(theirs) > 0
    border = ours[(ours.sensor == "VIIRS") & (ours.cell_id == "37.0_9.5")]  # both country files
    assert border[["n", "days"]].values.tolist() == [[4, 2]]  # 2015-08-04 counted once
