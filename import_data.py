"""Load an Excel/CSV file into the SQLite database (one table per sheet).

Command line:   python import_data.py my_data.xlsx            (replaces cricket.db)
                python import_data.py my_data.xlsx --merge    (keeps other tables, replaces same-named ones)
"""
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd

import crickslab_etl

SUPPORTED = (".xlsx", ".xlsm", ".csv")


def _clean_name(s, fallback):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(s).strip()).strip("_").lower()
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "c_" + s
    return s


def _unique(name, used):
    base, i = name, 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def _clean_df(df):
    df = df.dropna(how="all").dropna(axis=1, how="all").copy()
    seen = set()
    df.columns = [_unique(_clean_name(c, f"col_{i + 1}"), seen) for i, c in enumerate(df.columns)]
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            nn = s.dropna()
            midnight = (nn.dt.normalize() == nn).all()
            df[c] = s.dt.strftime("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M:%S")
        elif s.dtype == object:
            df[c] = s.map(lambda v: v.strip() if isinstance(v, str)
                          else (v.isoformat() if hasattr(v, "isoformat") else v))
    return df


def _read(path, table_hint):
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return pd.read_excel(path, sheet_name=None)
    if ext == ".csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="latin-1")
        return {table_hint or path.stem: df}
    raise ValueError(f"Unsupported file type {ext}. Use .xlsx or .csv (re-save .xls files as .xlsx).")


def import_file(path, db_path, fresh=True, table_hint=None):
    path, db_path = Path(path), Path(db_path)
    # The Crickslab player workbook (6 sheets) gets the dedicated loader: typed columns, cleaned values,
    # a proper matches table. Any other Excel/CSV file goes through the generic one-table-per-sheet path below.
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        with pd.ExcelFile(path) as xf:
            names = xf.sheet_names
        if crickslab_etl.looks_like_crickslab(names):
            return crickslab_etl.import_crickslab(path, db_path)
    sheets = _read(path, table_hint)
    tmp = db_path.with_name(db_path.name + ".new")
    if tmp.exists():
        tmp.unlink()
    if not fresh and db_path.exists():
        shutil.copy(db_path, tmp)
    con = sqlite3.connect(tmp)
    summary, used = {}, set()
    try:
        for raw_name, df in sheets.items():
            df = _clean_df(df)
            if len(df.columns) == 0:
                continue
            name = _unique(_clean_name(raw_name, "table"), used)
            df.to_sql(name, con, if_exists="replace", index=False)
            summary[name] = {"rows": int(len(df)), "columns": list(df.columns)}
        con.commit()
    finally:
        con.close()
    if not summary:
        tmp.unlink(missing_ok=True)
        raise ValueError("No data found in the file.")
    os.replace(tmp, db_path)
    return summary


if __name__ == "__main__":
    import db
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    result = import_file(args[0], db.DB_PATH, fresh="--merge" not in sys.argv)
    for t, info in result.items():
        print(f"  {t}: {info['rows']} rows, columns: {', '.join(info['columns'])}")
    print(f"Done. Database: {db.DB_PATH}")
