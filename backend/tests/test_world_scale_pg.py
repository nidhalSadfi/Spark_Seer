"""World-scale data paths (spec §16) on a real PostgreSQL (TEST_DATABASE_URL), against ORACLES.

The COPY-based readers and writers must give exactly what the row-based code of 538aa86 gave.
That code is kept here, verbatim, as the reference (`ref_*`). All data is SYNTHETIC: random
world-wide detections from tests/world_bench.py plus hand-placed edge cases (cell edges, the
antimeridian, the poles, NULL and empty text, SP types).
"""
import math
import struct
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402

import pgcopy  # noqa: E402
import world_bench  # noqa: E402
from processing.calibration import Calibration  # noqa: E402
from processing.common import KEY_COLS, month_bounds  # noqa: E402
from processing.grid import with_keys  # noqa: E402
from processing.mask import StaticMask, square_index  # noqa: E402
from processing.params import DEFAULT_PARAMS, ProcessingParams  # noqa: E402
from processing.pipeline import process  # noqa: E402
from test_worker_pg import pg, pg_url  # noqa: E402,F401
from worker import process_pending as pp  # noqa: E402
from worker import store  # noqa: E402

pytestmark = pytest.mark.pg
NOW = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)


# -- the reference: worker/process_pending.py and worker/store.py at 538aa86 ------------------------

def ref_load_pending(engine):
    with engine.connect() as c:
        rows = c.execute(text("SELECT id, content_hash, latitude, longitude, acq_date FROM raw_detections "
                              "WHERE needs_processing ORDER BY id")).all()
    return pd.DataFrame(rows, columns=["id", "content_hash", "latitude", "longitude", "acq_date"])


def ref_load_detections(engine, keys, params=DEFAULT_PARAMS):
    frames = []
    with engine.connect() as c:
        for (year, month), group in keys.groupby(["year", "month"]):
            first, last = month_bounds(int(year), int(month))
            rows = c.execute(text("""
                SELECT source, product, satellite, latitude, longitude, acq_date, acq_time_raw AS acq_time,
                       confidence, "type", instrument
                FROM raw_detections
                WHERE acq_date BETWEEN :first AND :last AND latitude >= :lat0 AND latitude < :lat1
                  AND longitude >= :lon0 AND longitude < :lon1"""),
                {"first": first, "last": last,
                 "lat0": group.cy.min() * params.cell_deg - 0.01, "lat1": (group.cy.max() + 1) * params.cell_deg + 0.01,
                 "lon0": group.cx.min() * params.cell_deg - 0.01, "lon1": (group.cx.max() + 1) * params.cell_deg + 0.01,
                 }).all()
            if rows:
                frames.append(pd.DataFrame(rows, columns=pp.DETECTION_COLUMNS))
    if not frames:
        return ref_detection_frame(pd.DataFrame(columns=pp.DETECTION_COLUMNS))
    df = pd.concat(frames, ignore_index=True)
    keyed = with_keys(df, params)
    inside = keyed[KEY_COLS].merge(keys, on=KEY_COLS, how="left", indicator=True)["_merge"].eq("both").to_numpy()
    return ref_detection_frame(df[inside])


def ref_detection_frame(df):
    out = df[pp.DETECTION_COLUMNS].reset_index(drop=True).copy()
    out["latitude"] = out["latitude"].astype(float)
    out["longitude"] = out["longitude"].astype(float)
    out["type"] = pd.array([None if pd.isna(v) else int(v) for v in out["type"]], dtype="Int64")
    return out


def ref_clear_pending(c, processed):
    if not processed:
        return 0
    return c.execute(text("""
        UPDATE raw_detections AS r SET needs_processing = false
        FROM unnest(CAST(:ids AS bigint[]), CAST(:hashes AS text[])) AS p(id, content_hash)
        WHERE r.id = p.id AND r.content_hash = p.content_hash AND r.needs_processing"""),
        {"ids": [int(i) for i, _ in processed], "hashes": [h for _, h in processed]}).rowcount


# -- synthetic data --------------------------------------------------------------------------------

EDGES = [  # (source, satellite, lat, lon, day, confidence, type)
    ("VIIRS_NOAA20_NRT", "N20", 36.5, 9.0, 3, "n", None),            # on a cell's south-west corner
    ("VIIRS_NOAA20_NRT", "N20", 36.49999, 8.99999, 3, "h", None),     # just inside the neighbour
    ("MODIS_NRT", "Aqua", 35.2, -0.00001, 4, "30", None),             # west of 0: cx = -1
    ("MODIS_NRT", "Terra", -0.00001, 0.0, 4, "29", None),             # south of 0: cy = -1
    ("VIIRS_NOAA21_NRT", "N21", -33.95, 18.5, 5, "nominal", None),    # southern hemisphere
    ("VIIRS_NOAA21_NRT", "N21", 64.1, 179.99999, 6, "high", None),    # the antimeridian
    ("VIIRS_NOAA21_NRT", "N21", 64.1, -180.0, 6, " L ", None),
    ("VIIRS_NOAA20_NRT", "N20", -89.99, -179.5, 7, "n", None),        # near the south pole
    ("VIIRS_NOAA20_NRT", "N20", 1e-5, 1e-5, 8, None, None),           # NULL confidence
    ("VIIRS_NOAA20_NRT", "N20", 12.34567, 23.45678, 9, "", None),     # empty confidence
    ("MODIS_SP", "Aqua", 12.3, 23.4, 9, "88", 0),                     # SP with types
    ("MODIS_SP", "Aqua", 12.3, 23.41, 9, "88", 2),
    ("VIIRS_SNPP_SP", "N", 12.3, 23.42, 2, "n", 3),
    ("VIIRS_SNPP_NRT", "N", 0.1 + 0.2, 1 / 3, 1, "n", None),          # doubles with long expansions
]


def edge_frame(fetch_id: int, job_id: int) -> pd.DataFrame:
    rows = []
    for i, (source, sat, lat, lon, day, conf, typ) in enumerate(EDGES):
        rows.append({"source": source, "satellite": sat, "latitude_raw": repr(lat), "longitude_raw": repr(lon),
                     "acq_date_raw": f"2026-11-{day:02d}", "acq_time_raw": f"{i:04d}", "content_hash": f"edge{i}",
                     "product": source.rsplit("_", 1)[1], "latitude": lat, "longitude": lon,
                     "acq_date": f"2026-11-{day:02d}", "acq_time": f"00:{i:02d}",
                     "instrument": "MODIS" if source.startswith("MODIS") else "VIIRS", "confidence": conf,
                     "version": "2.0NRT", "frp": None if i % 3 else 1.5, "daynight": "D",
                     "type": typ, "delivered": "{}", "first_seen_fetch_id": fetch_id, "last_seen_fetch_id": fetch_id,
                     "changed_in_job_id": job_id, "needs_processing": True})
    return pd.DataFrame(rows, columns=world_bench.RAW_COLUMNS)


def store_frame(engine, df: pd.DataFrame) -> None:
    with engine.begin() as c:
        pgcopy.write_table(c, "raw_detections", df)


@pytest.fixture
def world(pg):
    """~60k random world-wide detections of November 2026 (plus October rows outside the keys) and
    the edge cases, all pending except one month of non-pending neighbours."""
    rng = np.random.default_rng(3)
    job_id, fetch_ids = world_bench.seed_jobs_and_fetches(pg.engine)
    cells = world_bench.world_cells(rng, 3000)
    dets = world_bench.detections_frame(rng, cells, 60_000, fetch_ids, job_id)
    dets["needs_processing"] = np.where(rng.random(len(dets)) < 0.7, "t", "f")  # all cells' rows still load
    october = world_bench.detections_frame(rng, cells[:500], 3_000, fetch_ids, job_id)
    october["acq_date_raw"] = october["acq_date"] = october["acq_date_raw"].str.replace("2026-11", "2026-10")
    october["needs_processing"] = "f"
    both = pd.concat([dets, october], ignore_index=True)
    both["type"] = None  # NRT rows have no type (the bench writes "" for NULL with its own CSV writer)
    store_frame(pg.engine, both)
    store_frame(pg.engine, edge_frame(fetch_ids["MODIS_NRT"], job_id))
    return pg


def sort_detections(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["acq_date"] = pd.to_datetime(out["acq_date"]).astype("datetime64[ns]")
    return out.sort_values(["source", "satellite", "latitude", "longitude", "acq_date", "acq_time"],
                           ignore_index=True)


def float_bits(values) -> list[bytes]:
    return [struct.pack("<d", float(v)) for v in values]


# -- oracle tests ------------------------------------------------------------------------------------

def test_load_pending_and_affected_keys_equal_the_row_based_reference(world):
    new, ref = pp.load_pending(world.engine), ref_load_pending(world.engine)
    assert len(new) == len(ref) > 40_000
    assert new.id.tolist() == ref.id.tolist() and new.content_hash.tolist() == ref.content_hash.tolist()
    assert float_bits(new.latitude) == float_bits(ref.latitude)
    assert float_bits(new.longitude) == float_bits(ref.longitude)
    assert [d.date() for d in new.acq_date] == list(ref.acq_date)
    pd.testing.assert_frame_equal(pp.affected_keys(new), pp.affected_keys(ref))


@pytest.mark.parametrize("subset", ["all", "some"])
def test_load_detections_equals_the_row_based_reference(world, subset):
    keys = pp.affected_keys(pp.load_pending(world.engine))
    if subset == "some":  # a few cells only (the small-region case): the pre-selection must not lose any row
        keys = keys.iloc[::37].reset_index(drop=True)
    new, ref = pp.load_detections(world.engine, keys), ref_load_detections(world.engine, keys)
    assert list(new.columns) == list(ref.columns) == pp.DETECTION_COLUMNS
    assert str(new["type"].dtype) == str(ref["type"].dtype) == "Int64"
    a, b = sort_detections(new), sort_detections(ref)
    assert len(a) == len(b) > (40 if subset == "some" else 59_000)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert float_bits(a.latitude) == float_bits(b.latitude) and float_bits(a.longitude) == float_bits(b.longitude)


def test_the_whole_run_is_identical_on_both_loaders(world):
    """filter -> aggregate -> harmonize -> completeness -> gates -> drift on the COPY frame and on
    the reference frame: the same staged rows, gate results, flags and summaries."""
    pending = pp.load_pending(world.engine)
    keys = pp.affected_keys(pending)
    cal = Calibration(version="1", k_table=pd.DataFrame({"cy": [72], "cx": [17], "month": [11], "k": [3.1],
                                                         "support": [40.0]}),
                      k_month={m: 3.0 for m in range(1, 13)}, snpp_end=date(2026, 11, 2)).with_global_r(0.983,
                                                                                                    version="1")
    lat = pending.latitude.to_numpy()[::50]
    lon = pending.longitude.to_numpy()[::50]
    mask = StaticMask(version="m", res_deg=0.01, min_count=3, types=(1, 2, 3), input={},
                      squares=tuple(sorted({(int(a), int(b), 3) for a, b in zip(square_index(lat, 0.01),
                                                                                  square_index(lon, 0.01))})))
    coverage = [(f, date(2026, 11, 1), 30) for f in ("MODIS", "NOAA20", "NOAA21")] + [("SNPP", date(2026, 11, 1), 2)]
    avail = {"MODIS": date(2026, 11, 30), "NOAA20": date(2026, 11, 30), "NOAA21": date(2026, 11, 30),
             "SNPP": date(2026, 11, 2)}
    for params in (DEFAULT_PARAMS, ProcessingParams(min_lat=None)):
        runs = [process(frame, keys, cal, mask=mask, boundary=None, coverage=coverage, availability=avail, now=NOW,
                        params=params)
                for frame in (pp.load_detections(world.engine, keys, params),
                              ref_load_detections(world.engine, keys, params))]
        new, ref = runs
        pd.testing.assert_frame_equal(new.staged, ref.staged, check_exact=True)
        assert new.summary() == ref.summary()
        assert new.passed and new.stage_summaries["filter"]["dropped_total"] > 0


def test_clear_pending_by_copy_equals_the_reference(world):
    """Only rows still carrying the hash they were processed with are cleared; a row modified since
    keeps its flag. The COPY path (frame) and the unnest reference give the same rows."""
    pending, snapshot = ref_load_pending(world.engine), pp.load_pending(world.engine)
    with world.engine.begin() as c:  # modified after the snapshot was taken
        c.execute(text("UPDATE raw_detections SET content_hash = 'changed' WHERE id IN "
                       "(SELECT id FROM raw_detections WHERE needs_processing ORDER BY id LIMIT 25)"))
    results = {}
    for name, clear, processed in (("ref", ref_clear_pending, list(zip(pending.id, pending.content_hash))),
                                   ("copy", store._clear_pending, snapshot),
                                   ("pairs", store._clear_pending, list(zip(pending.id, pending.content_hash)))):
        with world.engine.connect() as c:
            tx = c.begin()
            n = clear(c, processed)
            left = c.execute(text("SELECT id FROM raw_detections WHERE needs_processing ORDER BY id")).scalars().all()
            tx.rollback()
        results[name] = (n, left)
    assert results["ref"] == results["copy"] == results["pairs"]
    assert len(results["ref"][1]) == 25 and results["ref"][0] == len(pending) - 25
    with world.engine.connect() as c:
        assert store._clear_pending(c, []) == 0 and store._clear_pending(c, pending.iloc[:0]) == 0


def test_pgcopy_round_trips_doubles_nulls_and_text(pg):
    rng = np.random.default_rng(5)
    values = np.concatenate([rng.normal(0, 1e3, 2000), rng.uniform(-180, 180, 2000), 1 / rng.integers(1, 10**6, 500),
                             [0.1 + 0.2, 1 / 3, 2 ** -1074, 1.7976931348623157e308, -0.0, 5e-324, 123456789.12345678]])
    texts = ["", None, "a,b", 'q"uote', "new\nline", "ünïcode", " spaced "] * (len(values) // 7 + 1)
    frame = pd.DataFrame({"x": values, "t": texts[:len(values)], "i": np.arange(len(values)),
                          "n": pd.array([None if i % 4 == 0 else i for i in range(len(values))], dtype="Int64")})
    with pg.engine.begin() as c:
        c.execute(text("CREATE TABLE copy_check (x double precision, t text, i bigint, n bigint)"))
        assert pgcopy.write_table(c, "copy_check", frame) == len(frame)
    with pg.engine.connect() as c:
        back = pgcopy.read_frame(c, "SELECT x, t, i, n FROM copy_check ORDER BY i", None,
                                 {"x": pa.float64(), "t": pa.string(), "i": pa.int64(), "n": pa.int64()},
                                 nullable_int=("n",))
        rows = c.execute(text("SELECT x, t, n FROM copy_check ORDER BY i")).all()
    assert float_bits(back.x) == float_bits(values) == float_bits([r.x for r in rows])
    assert back.t.tolist() == [r[1] for r in rows] == frame.t.tolist()  # "" stays "", NULL stays None
    assert [None if v is pd.NA else int(v) for v in back.n] == [r[2] for r in rows]
    assert math.copysign(1, back.x.iloc[len(values) - 3]) == -1  # -0.0 kept
    with pg.engine.connect() as c:
        empty = pgcopy.read_frame(c, "SELECT x, t FROM copy_check WHERE false", None,
                                  {"x": pa.float64(), "t": pa.string()})
    assert list(empty.columns) == ["x", "t"] and empty.empty


def test_world_bench_helpers_cover_the_month(world):
    """Sanity check of the synthetic generator the benchmark uses (no NULL keys, November dates)."""
    with world.engine.connect() as c:
        lo, hi, n = c.execute(text("SELECT min(acq_date), max(acq_date), count(*) FROM raw_detections")).one()
    assert (lo, hi) == (date(2026, 10, 1), date(2026, 11, 30)) and n > 59_000
    assert hi - lo <= timedelta(days=60)


# -- bootstrap at world scale -----------------------------------------------------------------------

def world_parquet(folder, rows=40_000, seed=9, days=True):
    """A synthetic world harmonized.parquet (world_bench.history_parquet) with day columns and its
    validation and fit files; returns the bootstrap keyword arguments."""
    import json

    from processing.inter_viirs import FIT_KIND

    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    path = folder / "harmonized.parquet"
    df = world_bench.history_parquet(rng, path, world_bench.world_cells(rng, 800), rows)
    if days:
        df["MODIS_days"] = np.minimum(df.MODIS, 28).astype(float)
        df["VIIRS_days"] = np.minimum(df.VIIRS, 28).astype(float)
    df.loc[df.index[:3], "harmonized"] = [0.1 + 0.2, 1 / 3, 2.0 ** -30]  # long binary expansions survive
    df.to_parquet(path, index=False)
    (folder / "harmonized_validation.json").write_text(json.dumps({"error_pct_cell_month_ratio": 11.5,
                                                                    "synthetic": True}))
    fit = folder / "inter_viirs_fit.json"
    fit.write_text(json.dumps({"kind": FIT_KIND, "approved_r": 0.983, "r_global": 0.983}))
    return {"parquet": path, "validation": folder / "harmonized_validation.json", "inter_viirs": fit, "mask": None}


def db_cell_month(engine) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            f"SELECT {', '.join(store.CELL_COLUMNS)}, dataset_version_id FROM cell_month "
            "ORDER BY cy, cx, year, month")).mappings()]


def ref_calibration_k(c, cal_id):
    """worker/store.py's k-table read at 538aa86 (SQLAlchemy rows)."""
    return pd.DataFrame(c.execute(text("SELECT cy, cx, month, k, support FROM calibration_k "
                                       "WHERE calibration_version_id = :id ORDER BY cy, cx, month"),
                                  {"id": cal_id}).all(), columns=["cy", "cx", "month", "k", "support"])


@pytest.mark.parametrize("days", [True, False])
def test_parquet_frame_equals_parquet_rows(tmp_path, days):
    from worker.bootstrap import parquet_frame, parquet_rows

    args = world_parquet(tmp_path, rows=20_000, days=days)
    df = pd.read_parquet(args["parquet"])
    ref = parquet_rows(df, 7)
    new = [{k: (None if v is pd.NA else v) for k, v in r.items()} for r in parquet_frame(df, 7).to_dict("records")]
    for r in new:
        r["quality_flags"] = [] if r["quality_flags"] == "{}" else r["quality_flags"]
    assert len(new) == len(ref) == len(df)
    for a, b in zip(new, ref):
        assert a.keys() == b.keys()
        for k in a:
            assert (a[k] is None) == (b[k] is None) and (a[k] is None or a[k] == b[k]), (k, a, b)
            if isinstance(b[k], float):
                assert float_bits([a[k]]) == float_bits([b[k]])


def test_bootstrap_by_copy_stores_exactly_the_row_mapping(pg, tmp_path):
    from worker.bootstrap import bootstrap, parquet_rows

    args = world_parquet(tmp_path / "w")
    res = bootstrap(pg.engine, **args, now=NOW)
    assert res.created and res.rows == 40_000
    df = pd.read_parquet(args["parquet"]).sort_values(["cy", "cx", "year", "month"], ignore_index=True)
    expected = [{**r, "dataset_version_id": res.dataset_version_id} for r in parquet_rows(df, res.calibration_version_id)]
    got = db_cell_month(pg.engine)
    assert len(got) == len(expected)
    for a, b in zip(got, expected):
        assert a == b, (a, b)
        assert float_bits([a["harmonized"], a["k"], a["support"]]) == float_bits([b["harmonized"], b["k"], b["support"]])
    with pg.engine.connect() as c:
        cv = store.load_approved_calibration(c)  # the stored fingerprint matches the COPY read back
        ref = Calibration(version=str(cv.id), k_table=ref_calibration_k(c, cv.id), k_month=cv.calibration.k_month,
                          snpp_end=cv.calibration.snpp_end, r=cv.calibration.r, r_global=cv.calibration.r_global)
    assert ref.fingerprint == cv.calibration.fingerprint
    pd.testing.assert_frame_equal(cv.calibration.k_table, ref.k_table, check_exact=True)
    assert len(cv.calibration.k_table) == len(df.drop_duplicates(["cy", "cx", "month"]))


def table_rows(engine, table, order):
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(f"SELECT * FROM {table} ORDER BY {order}")).all()]


def test_bootstrap_replace_supersedes_in_one_transaction(pg, tmp_path, monkeypatch):
    from worker import bootstrap as boot

    first = boot.bootstrap(pg.engine, **world_parquet(tmp_path / "a", rows=5_000, seed=1), now=NOW)
    old_rows = db_cell_month(pg.engine)
    new_args = world_parquet(tmp_path / "b", rows=7_000, seed=2)
    # Without replace: nothing done (the refusal is unchanged).
    again = boot.bootstrap(pg.engine, **new_args, now=NOW)
    assert not again.created and "already bootstrapped" in again.message

    # A failure in the middle (after the history copy and the delete) leaves everything as it was.
    tables = {"cell_month": "cy, cx, year, month", "cell_month_history": "id", "dataset_versions": "id",
              "calibration_versions": "id", "calibration_k": "calibration_version_id, cy, cx, month"}
    before = {t: table_rows(pg.engine, t, o) for t, o in tables.items()}

    def boom(*a, **kw):
        raise RuntimeError("simulated crash during the reload")
    monkeypatch.setattr(store, "copy_cell_month", boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        boot.bootstrap(pg.engine, **new_args, now=NOW, replace=True)
    assert {t: table_rows(pg.engine, t, o) for t, o in tables.items()} == before
    monkeypatch.undo()

    res = boot.bootstrap(pg.engine, **new_args, now=NOW + timedelta(hours=1), replace=True)
    # (the failed attempt used up id 2 of each sequence)
    cid, did = res.calibration_version_id, res.dataset_version_id
    assert res.created and cid > 1 and did > 1 and res.rows == 7_000
    assert "replaced calibration version 1 and dataset version 1" in res.message
    with pg.engine.connect() as c:
        ds = c.execute(text("SELECT id, parent_id, status, calibration_version_id, summary FROM dataset_versions "
                            "ORDER BY id")).all()
        cal = c.execute(text("SELECT id, status, label, validation_report FROM calibration_versions ORDER BY id")).all()
        hist = c.execute(text("SELECT count(*), min(replaced_by_version), max(replaced_by_version), "
                              "min(dataset_version_id) FROM cell_month_history")).one()
        cv = store.load_approved_calibration(c)
    assert [(d.id, d.parent_id, d.status, d.calibration_version_id) for d in ds] == \
        [(1, None, "superseded", 1), (did, 1, "approved", cid)]
    assert ds[1].summary["source"] == "bootstrap_replace" and ds[1].summary["replaces_dataset_version"] == 1
    assert [(c_.id, c_.status, c_.label) for c_ in cal] == [(1, "superseded", "bootstrap"),
                                                           (cid, "approved", "bootstrap-replace")]
    assert cal[1].validation_report["replaces_calibration_version"] == 1 and cv.id == cid
    assert tuple(hist) == (len(old_rows), did, did, first.dataset_version_id)  # the old rows, kept for rollback
    new = db_cell_month(pg.engine)
    assert len(new) == 7_000 and {r["dataset_version_id"] for r in new} == {did}
    assert {r["calibration_version_id"] for r in new} == {cid}
    with pg.engine.connect() as c:  # the old calibration's k table is still there (never deleted)
        assert c.scalar(text("SELECT count(*) FROM calibration_k WHERE calibration_version_id = 1")) > 0


def test_bootstrap_cli_takes_world_paths_and_replace(pg, tmp_path):
    import os
    import subprocess
    import sys

    from sqlalchemy.engine import make_url

    a, b = world_parquet(tmp_path / "a", rows=2_000, seed=4), world_parquet(tmp_path / "b", rows=3_000, seed=5)
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}"})
    env = {k: v for k, v in os.environ.items() if k != "STATIC_MASK_FILE"}
    env["DATABASE_URL"] = url.render_as_string(hide_password=False)  # the test schema, never the real database

    def run(args, *extra):
        cmd = [sys.executable, "-m", "worker.bootstrap", "--data", str(args["parquet"]), "--validation",
               str(args["validation"]), "--inter-viirs", str(args["inter_viirs"]), *extra]
        r = subprocess.run(cmd, cwd=world_bench.BACKEND, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout + r.stderr

    assert "bootstrapped calibration version 1 and dataset version 1 (2000 cell-months)" in run(a)
    assert "already bootstrapped" in run(b)
    assert "with calibration version 2 and dataset version 2 (3000 cell-months" in run(b, "--replace")
    assert len(db_cell_month(pg.engine)) == 3_000
