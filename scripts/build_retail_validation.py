"""Derive the weekly series used to validate Meridian's cash-in forecasting on REAL transactions.

Source: "Online Retail II", UCI Machine Learning Repository (D. Chen, 2012; CC BY 4.0),
https://archive.ics.uci.edu/dataset/502/online+retail+ii — 1,067,371 invoice lines of a UK online retailer,
2009-12-01 to 2011-12-09. Download the ZIP, extract online_retail_II.xlsx (or a CSV export of both sheets), then:

    pip install openpyxl pandas      # only needed to rebuild this file
    python scripts/build_retail_validation.py path/to/online_retail_II.xlsx|.csv

Only weekly aggregates are kept in the repository (no customer data). Customer IDs are counted, never stored.
"""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "src" / "meridian" / "data" / "validation" / "online_retail_weekly.json"


def main(src: str) -> None:
    p = Path(src)
    raw_sha = hashlib.sha256(p.read_bytes()).hexdigest()
    df = (pd.concat(pd.read_excel(p, sheet_name=None).values(), ignore_index=True) if p.suffix == ".xlsx"
          else pd.read_csv(p))
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])
    df["value"] = df["Quantity"] * df["Price"]
    df["cancel"] = df["Invoice"].astype(str).str.startswith("C")
    # weeks Monday-start; the first and last partial weeks are dropped
    df["week"] = df["InvoiceDate"].dt.to_period("W-SUN").dt.start_time
    g = df.groupby("week")
    weekly = pd.DataFrame({
        "gross_invoiced_gbp": g.apply(lambda x: x.loc[~x["cancel"] & (x["value"] > 0), "value"].sum()),
        "cancellations_gbp": g.apply(lambda x: -x.loc[x["cancel"], "value"].sum()),
        "invoices": g["Invoice"].nunique(),
        "customers": g["Customer ID"].nunique(),
    })
    weekly["net_gbp"] = weekly["gross_invoiced_gbp"] - weekly["cancellations_gbp"]
    weekly = weekly.iloc[1:-1]
    body = {
        "source": "UCI Machine Learning Repository — Online Retail II (D. Chen), CC BY 4.0",
        "source_url": "https://archive.ics.uci.edu/dataset/502/online+retail+ii",
        "raw_file": p.name, "raw_sha256": raw_sha, "raw_rows": int(len(df)),
        "period": [str(weekly.index.min().date()), str(weekly.index.max().date())],
        "classification": "PUBLIC_REAL (anonymised retailer, real transactions)",
        "series": [{"week": str(w.date()), **{k: round(float(v), 2) for k, v in r.items()}} for w, r in weekly.iterrows()],
    }
    body["checksum_sha256"] = hashlib.sha256(json.dumps(body["series"], sort_keys=True).encode()).hexdigest()
    OUT.write_text(json.dumps(body, indent=0), encoding="utf-8")
    print(OUT, len(body["series"]), "weeks")


if __name__ == "__main__":
    main(sys.argv[1])
