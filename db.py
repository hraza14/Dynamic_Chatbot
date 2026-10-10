"""SQLite access: read-only connections, schema description for the LLM, safe query execution."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = Path(os.environ.get("CRICKET_DB", BASE / "cricket.db"))
DICT_PATH = BASE / "data_dictionary.txt"
QUERY_TIMEOUT_S = 15

_schema_cache = {"key": None, "text": ""}


def exists(db_path):
    return Path(db_path).exists() and Path(db_path).stat().st_size > 0


def connect_ro(db_path):
    db_path = Path(db_path)
    if not exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _json_safe(v):
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    return v


def get_schema(db_path, detail=False):
    if not exists(db_path):
        return []
    con = connect_ro(db_path)
    try:
        out = []
        kinds = {}
        names = [kinds.setdefault(r[0], r[1].upper()) and r[0] for r in con.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")]
        for t in names:
            cols = [{"name": r["name"], "type": (r["type"] or "").upper()}
                    for r in con.execute(f'PRAGMA table_info("{t}")')]
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            item = {"name": t, "kind": kinds.get(t, "TABLE"), "rows": n, "columns": cols}
            if detail:
                for c in cols:
                    if c["type"] in ("TEXT", ""):
                        cn = c["name"]
                        q = (f'SELECT DISTINCT "{cn}" FROM (SELECT "{cn}" FROM "{t}" LIMIT 1000000) '
                             f'WHERE "{cn}" IS NOT NULL LIMIT 13')
                        vals = [str(r[0])[:40] for r in con.execute(q)]
                        if 0 < len(vals) <= 12:
                            c["values"] = vals
                row = con.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
                if row:
                    item["sample"] = {k: _json_safe(v) for k, v in zip(row.keys(), row) if v is not None}
            out.append(item)
        return out
    finally:
        con.close()


def schema_text(db_path, max_chars=14000):
    """Compact schema description that goes into the LLM prompt (cached until the file changes)."""
    if not exists(db_path):
        return ""
    db_path = Path(db_path)
    key = (db_path.stat().st_mtime, DICT_PATH.stat().st_mtime if DICT_PATH.exists() else 0, str(db_path))
    if _schema_cache["key"] == key:
        return _schema_cache["text"]
    parts = []
    for t in get_schema(db_path, detail=True):
        lines = [f"{t.get('kind', 'TABLE')} {t['name']} ({t['rows']} rows)"]
        for c in t["columns"]:
            extra = ""
            if c.get("values"):
                extra = "   -- allowed values: " + ", ".join(repr(v) for v in c["values"])
            lines.append(f"  {c['name']} {c['type'] or 'TEXT'}{extra}")
        if t.get("sample"):
            lines.append("  example row: " + json.dumps(t["sample"], ensure_ascii=False, default=str))
        parts.append("\n".join(lines))
    text = "\n\n".join(parts)[:max_chars]
    if DICT_PATH.exists():
        text += "\n\nDATA DICTIONARY / NOTES FROM THE DATA OWNER\n" + DICT_PATH.read_text(encoding="utf-8")[:9000]
    _schema_cache.update(key=key, text=text)
    return text


def validate_sql(sql):
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        raise ValueError("The model returned an empty query.")
    if ";" in s:
        raise ValueError("Only a single SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ValueError("Only SELECT queries are allowed.")
    return s


def run_query(sql, db_path, max_rows=200):
    """Run a validated SELECT on a read-only connection with a time limit."""
    s = validate_sql(sql)
    con = connect_ro(db_path)
    try:
        deadline = time.time() + QUERY_TIMEOUT_S
        con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 100000)
        cur = con.execute(s)
        if cur.description is None:
            raise ValueError("The query did not return a result set.")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = [[_json_safe(v) for v in r] for r in rows[:max_rows]]
        return cols, rows, truncated
    finally:
        con.close()
