"""PostgreSQL COPY helpers (psycopg 3) for the world-scale paths (spec §16).

Moving millions of rows through SQLAlchemy Row objects (or one parameter dict per row) costs
gigabytes of Python objects and minutes. These helpers stream the rows as CSV through COPY and
let pyarrow do the parsing and formatting in C:

- `read_frame(conn, query, params, columns)`: `COPY (query) TO STDOUT` into a pandas frame typed by
  `columns`. Works inside any transaction, READ ONLY ones included (the API uses it).
- `write_table(conn, table, frame)`: a pandas frame into `table` with `COPY ... FROM STDIN`, in
  the caller's transaction.

Values survive the trip unchanged: PostgreSQL (>= 12) prints float8 in the shortest form that
reads back to the same double, and pyarrow parses and prints doubles exactly (both are checked
by tests/test_pgcopy_pg.py). NULL travels as an unquoted empty field, an empty string as "".

`conn` is a SQLAlchemy Connection on the psycopg 3 driver; the COPY runs on its DBAPI connection,
so it belongs to the same transaction as the connection's other statements. Queries use psycopg
placeholders (%(name)s), merged client-side by psycopg.
"""

from __future__ import annotations

import io
from typing import Mapping

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv

BLOCK_SIZE = 1 << 24  # pyarrow CSV block; rows are parsed in parallel blocks


def _cursor(conn):
    return conn.connection.dbapi_connection.cursor()


def read_table(conn, query: str, params: Mapping | None, columns: Mapping[str, pa.DataType]) -> pa.Table:
    """Rows of `query` (its SELECT list in `columns` order) as a pyarrow Table typed by `columns`."""
    buf = io.BytesIO()
    with _cursor(conn) as cur:
        with cur.copy(f"COPY ({query}) TO STDOUT (FORMAT csv)", params or None) as cp:
            for chunk in cp:
                buf.write(chunk)
    names = list(columns)
    if buf.tell() == 0:
        return pa.table({n: pa.array([], type=t) for n, t in columns.items()})
    return pacsv.read_csv(
        pa.BufferReader(buf.getbuffer()),
        read_options=pacsv.ReadOptions(column_names=names, block_size=BLOCK_SIZE),
        # Text values may hold a quoted newline in principle (FIRMS never sends one): parse it, never split on it.
        parse_options=pacsv.ParseOptions(newlines_in_values=True),
        convert_options=pacsv.ConvertOptions(column_types=dict(columns), null_values=[""], strings_can_be_null=True,
                                             quoted_strings_can_be_null=False))


def read_frame(conn, query: str, params: Mapping | None, columns: Mapping[str, pa.DataType], *,
               nullable_int: tuple[str, ...] = (), arrow_strings: tuple[str, ...] = (),
               dates_as_datetime64: bool = True) -> pd.DataFrame:
    """`read_table` as a pandas frame: strings as Python str objects (deduplicated by pyarrow), as
    a SQLAlchemy result would give them, except the columns in `arrow_strings`, which stay in
    Arrow memory (pandas "string[pyarrow]": no Python object per value, for millions of distinct
    strings such as content hashes). Integer columns named in `nullable_int` become Int64 (NULL =
    <NA>); the other integer columns must be NOT NULL. Dates become datetime64 (or datetime.date
    objects with dates_as_datetime64=False)."""
    table = read_table(conn, query, params, columns)
    keep = {name: table.column(name) for name in arrow_strings}
    frame = table.drop_columns(list(keep)).to_pandas(date_as_object=not dates_as_datetime64) if keep else \
        table.to_pandas(date_as_object=not dates_as_datetime64)
    for name, col in keep.items():
        frame[name] = pd.Series(pd.arrays.ArrowStringArray(col.combine_chunks() if col.num_chunks != 1
                                                           else col.chunk(0)) if len(col) else
                                pd.array([], dtype="string[pyarrow]"), index=frame.index)
    frame = frame[list(columns)]
    for name in nullable_int:
        frame[name] = pd.array(table.column(name), dtype="Int64") if table.num_rows else \
            pd.array([], dtype="Int64")
    return frame


def empty_frame(columns: Mapping[str, pa.DataType], *, nullable_int: tuple[str, ...] = (),
                dates_as_datetime64: bool = True) -> pd.DataFrame:
    """The zero-row frame read_frame returns for these columns."""
    frame = pa.table({n: pa.array([], type=t) for n, t in columns.items()}).to_pandas(
        date_as_object=not dates_as_datetime64)
    for name in nullable_int:
        frame[name] = pd.array([], dtype="Int64")
    return frame


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pacsv.write_csv(table, out, write_options=pacsv.WriteOptions(include_header=False, quoting_style="needed"))
    return out.getvalue()


def write_table(conn, table: str, frame: pd.DataFrame, chunk_rows: int = 500_000) -> int:
    """COPY `frame` (its columns named like the table's) into `table`; returns the rows written.

    NULL is None / NaN / <NA>; a text[] column holds PostgreSQL array literals (e.g. "{}"). Floats
    are written in pyarrow's shortest round-trip form.
    """
    if frame.empty:
        return 0
    cols = ", ".join(f'"{c}"' for c in frame.columns)
    with _cursor(conn) as cur:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
            for start in range(0, len(frame), chunk_rows):
                cp.write(_csv_bytes(frame.iloc[start:start + chunk_rows]))
    return len(frame)
