"""Step 2: fit MODIS -> VIIRS ratio per cell and calendar month, validate on held-out years.

Usage:
    python pipeline/calibrate.py data/cells.parquet data/harmonized.parquet

Model (KISS): VIIRS_equiv = k * MODIS, where for each cell and calendar month
    k = (V + M_PSEUDO * k_month) / (M + M_PSEUDO)
V, M = summed counts over training years; k_month = region-wide ratio for that calendar month.
The pseudo-count shrinks sparse cells toward the regional ratio instead of producing wild values.
"""
import json
import sys

import numpy as np
import pandas as pd

TRAIN = range(2012, 2020)   # fit years (overlap period)
TEST = range(2020, 2022)    # held-out validation years
M_PSEUDO = 20.0             # shrinkage strength, in MODIS detections


def fit(df: pd.DataFrame, years) -> pd.DataFrame:
    """Return k per (cy, cx, month)."""
    d = df[df.year.isin(years)]
    piv = d.pivot_table(index=["cy", "cx", "month"], columns="sensor", values="n",
                        aggfunc="sum", fill_value=0).reset_index()
    piv = piv.rename(columns={"MODIS": "M", "VIIRS": "V"})
    for c in ("M", "V"):
        if c not in piv:
            piv[c] = 0
    reg = piv.groupby("month")[["M", "V"]].sum()
    k_month = (reg.V / reg.M.replace(0, np.nan)).fillna((reg.V.sum() / max(reg.M.sum(), 1)))
    piv["k_month"] = piv.month.map(k_month)
    piv["k"] = (piv.V + M_PSEUDO * piv.k_month) / (piv.M + M_PSEUDO)
    piv["support"] = piv.M  # how much MODIS data the cell's ratio rests on
    return piv[["cy", "cx", "month", "k", "k_month", "support"]], k_month


def monthly(df: pd.DataFrame, sensor: str) -> pd.DataFrame:
    return (df[df.sensor == sensor].groupby(["cy", "cx", "year", "month"]).n.sum()
            .rename(sensor).reset_index())


def validate(df: pd.DataFrame) -> dict:
    """Predict held-out VIIRS from MODIS and compare against two baselines, at region-month level."""
    k_cell, k_month = fit(df, TRAIN)
    k_global = float(df[df.year.isin(TRAIN) & (df.sensor == "VIIRS")].n.sum()
                     / max(df[df.year.isin(TRAIN) & (df.sensor == "MODIS")].n.sum(), 1))
    test = df[df.year.isin(TEST)]
    m = monthly(test, "MODIS").merge(k_cell, on=["cy", "cx", "month"], how="left")
    m["k"] = m.k.fillna(m.month.map(k_month)).fillna(k_global)
    m["pred"] = m.MODIS * m.k
    pred = m.groupby(["year", "month"]).agg(pred=("pred", "sum"), raw=("MODIS", "sum"))
    obs = monthly(test, "VIIRS").groupby(["year", "month"]).VIIRS.sum()
    t = pred.join(obs, how="outer").fillna(0)
    t["global"] = t.raw * k_global

    def mae_pct(col):  # error as % of mean observed monthly VIIRS count
        return round(100 * float((t[col] - t.VIIRS).abs().mean() / max(t.VIIRS.mean(), 1)), 1)

    return {
        "train_years": [TRAIN.start, TRAIN.stop - 1],
        "test_years": [TEST.start, TEST.stop - 1],
        "error_pct_uncorrected": mae_pct("raw"),
        "error_pct_global_ratio": mae_pct("global"),
        "error_pct_cell_month_ratio": mae_pct("pred"),
        "k_global": round(k_global, 2),
        "test_months": int(len(t)),
    }


def harmonize(df: pd.DataFrame) -> pd.DataFrame:
    """Final series: VIIRS-equivalent counts. Before 2012 = k * MODIS; from 2012 = observed VIIRS."""
    k_cell, k_month = fit(df, range(2012, 2022))  # refit on all clean overlap years
    mo = monthly(df, "MODIS")
    vi = monthly(df, "VIIRS")
    out = mo.merge(vi, on=["cy", "cx", "year", "month"], how="outer").fillna(0)
    out = out.merge(k_cell, on=["cy", "cx", "month"], how="left")
    out["k"] = out.k.fillna(out.month.map(k_month)).fillna(1.0)
    out["support"] = out.support.fillna(0)
    out["harmonized"] = np.where(out.year < 2012, out.MODIS * out.k, out.VIIRS)
    out["source"] = np.where(out.year < 2012, "MODIS*k", "VIIRS")
    return out


def main(in_path: str, out_path: str) -> None:
    df = pd.read_parquet(in_path)
    report = validate(df)
    print(json.dumps(report, indent=2))
    with open(out_path.replace(".parquet", "_validation.json"), "w") as f:
        json.dump(report, f, indent=2)
    harmonize(df).to_parquet(out_path, index=False)
    print("wrote", out_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
