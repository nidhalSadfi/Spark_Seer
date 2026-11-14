"""Step 1: FIRMS yearly country CSVs -> monthly counts per 0.5 deg cell and sensor.

Usage:
    python pipeline/ingest.py data/raw data/cells.parquet [min_lat]

min_lat (optional, e.g. 34): drop detections south of this latitude. For Tunisia/Algeria, half of the
VIIRS "vegetation fire" detections sit on Saharan oil and gas fields (e.g. ~31.5N 5.5-6E) that the
type flag misses; the vegetation-fire belt is north of 34N.

Put the unzipped FIRMS CSVs (e.g. modis_2015_Tunisia.csv, viirs-snpp_2015_Tunisia.csv)
anywhere under data/raw/. The sensor is read from the `instrument` column, not the file name.
"""
import sys
import duckdb

CELL_DEG = 0.5  # grid size in degrees


def main(raw_dir: str, out_path: str, min_lat: float = -90.0) -> None:
    con = duckdb.connect()
    # union_by_name: MODIS and VIIRS files have different brightness columns.
    con.execute(f"""
        CREATE TABLE det AS
        SELECT * FROM read_csv_auto('{raw_dir}/**/*.csv',
                                    union_by_name=true, all_varchar=true)
    """)

    con.execute(f"""
        COPY (
          SELECT
            CAST(floor(CAST(latitude AS DOUBLE) / {CELL_DEG}) AS INT) AS cy,
            CAST(floor(CAST(longitude AS DOUBLE) / {CELL_DEG}) AS INT) AS cx,
            CAST(strftime(CAST(acq_date AS DATE), '%Y') AS INT) AS year,
            CAST(strftime(CAST(acq_date AS DATE), '%m') AS INT) AS month,
            upper(instrument) AS sensor,           -- 'MODIS' or 'VIIRS'
            count(*) AS n
          FROM det
          WHERE
            -- type 0 = presumed vegetation fire (drops volcanoes, gas flares, offshore)
            coalesce(type, '0') = '0'
            AND CAST(latitude AS DOUBLE) >= {min_lat}
            AND (
              (upper(instrument) = 'MODIS' AND TRY_CAST(confidence AS INT) >= 30)
              OR (upper(instrument) = 'VIIRS' AND lower(left(confidence, 1)) IN ('n', 'h'))
            )
            -- keep only Suomi NPP for VIIRS so the reference sensor is one instrument
            AND (upper(instrument) = 'MODIS' OR upper(satellite) IN ('N', 'NPP', 'SUOMI NPP', 'S-NPP'))
          GROUP BY ALL
        ) TO '{out_path}' (FORMAT parquet)
    """)

    summary = con.execute(f"""
        SELECT sensor, min(year), max(year), sum(n) AS detections, count(DISTINCT (cy, cx)) AS cells
        FROM '{out_path}' GROUP BY sensor ORDER BY sensor
    """).fetchall()
    for row in summary:
        print("sensor=%s years=%s-%s detections=%s cells=%s" % row)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else -90.0)
