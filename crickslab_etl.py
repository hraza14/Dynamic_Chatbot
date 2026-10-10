"""Load the Crickslab player workbook (crickslab_players.xlsx) into a clean, typed SQLite schema.

The raw workbook has six sheets and every value is stored as text, with '--' / '-' as "missing" markers.
Some sheets are not what their name suggests (e.g. "Matches" is one row per player *appearance*), so this
module remodels the data instead of copying sheets 1:1:

    General Info  -> players
    Batting Stats -> batting_career (the 'AllTime' row) + batting_by_format (all other rows)
    Bowling Stats -> bowling_career + bowling_by_format
    Fielding      -> fielding
    Matches       -> matches (one row per match) + player_matches (who played in which match)
    Awards        -> awards
    plus views    -> player_career (one row per player, the easiest table for most questions)

Run directly:  python crickslab_etl.py crickslab_players.xlsx [cricket.db]
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EXPECTED_SHEETS = {"general_info", "batting_stats", "bowling_stats", "fielding_stats", "matches", "awards"}


def _key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def looks_like_crickslab(sheet_names):
    """True if a workbook has the six Crickslab sheets (so the dedicated loader should be used)."""
    return EXPECTED_SHEETS.issubset({_key(n) for n in sheet_names})


# ---------------------------------------------------------------- helpers
def _text(s):
    """Strip; turn '', '-', '--' and 'nan' into NULL."""
    s = s.astype("string").str.strip()
    return s.mask(s.isin(["", "-", "--", "nan", "None"]))


def _num(s):
    return pd.to_numeric(_text(s), errors="coerce")


def _int(s):
    return _num(s).astype("Int64")


def format_group(label):
    """Collapse the ~550 free-text competition labels into a few comparable groups."""
    t = str(label).upper()
    if re.search(r"\bT\s?20\b", t):
        return "T20"
    if re.search(r"\bT\s?10\b", t):
        return "T10"
    if re.search(r"\bODI\b|ONE DAY|50 OVERS", t):
        return "ODI"
    if re.search(r"\bT\s?40\b", t):
        return "T40"
    if re.search(r"\bT\s?30\b", t):
        return "T30"
    if re.search(r"TEST|3 DAYS|2 DAYS|MULTI", t):
        return "Test / multi-day"
    return "Other"


_RESULT = re.compile(r"^(?P<winner>.+?)\s+won\s+by\s+(?:(?P<n>\d+)\s+(?P<unit>runs?|wickets?)(?P<inn>\s+and\s+an\s+inning)?|(?P<so>super\s+over))", re.I)
_URL_TIME = re.compile(r"-(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(\d{1,2})([A-Za-z]{3})(\d{4})-(\d{2})(\d{2})(AM|PM)", re.I)


def _norm_team(t):
    return re.sub(r"\s+", " ", str(t)).strip().upper()


def _winner_side(winner, t1, t2):
    """Best effort: which listed team is the winner? NULL when the names cannot be matched confidently."""
    w, a, b = _norm_team(winner), _norm_team(t1), _norm_team(t2)
    if w == a and w != b:
        return "team1"
    if w == b and w != a:
        return "team2"
    in_a = bool(w) and (w in a or a in w)
    in_b = bool(w) and (w in b or b in w)
    if in_a != in_b:
        return "team1" if in_a else "team2"
    return None


def _parse_results(m):
    ext = m["result"].str.extract(_RESULT)
    out = pd.DataFrame(index=m.index)
    out["winner_name"] = ext["winner"].str.replace(r"\s+", " ", regex=True).str.strip()
    out["win_margin"] = pd.to_numeric(ext["n"], errors="coerce").astype("Int64")
    unit = ext["unit"].str.lower().str.rstrip("s") + "s"          # 'runs' / 'wickets'
    mtype = np.where(ext["inn"].notna(), "innings and runs", unit.astype(object))
    mtype = np.where(ext["so"].notna(), "super over", mtype)
    out["win_margin_type"] = pd.Series(mtype, index=m.index, dtype=object)
    out.loc[ext["winner"].isna(), "win_margin_type"] = None
    out["winner_side"] = [
        _winner_side(w, a, b) if isinstance(w, str) else None
        for w, a, b in zip(out["winner_name"], m["team1"], m["team2"])
    ]
    return out


def _local_datetime_from_url(url):
    """The URL carries the *local* start time (e.g. Sat19Feb2022-0900AM); the sheet's datetime is UTC."""
    mt = _URL_TIME.search(url or "")
    if not mt:
        return pd.NaT
    d, mon, y, hh, mm, ap = mt.groups()
    return pd.to_datetime(f"{d} {mon} {y} {hh}:{mm} {ap}", format="%d %b %Y %I:%M %p", errors="coerce")


# ---------------------------------------------------------------- table builders
def build_players(g):
    p = pd.DataFrame({
        "player_id": _int(g["player_id"]),
        "name": _text(g["name"]),
        "nationality": _text(g["nationality"]),
        "role": _text(g["role"]),
        "batting_style": _text(g["batting_style"]),
        "bowling_style": _text(g["bowling_style"]),
        "city": _text(g["city"]),
        "birth_place": _text(g["birth_place"]),
        "height_raw": _text(g["height"].replace(r"^\s*ft\s+inches\s*$", "--", regex=True)),
        "total_matches": _int(g["total_matches"]),
        "total_runs": _int(g["total_runs"]),
        "total_wickets": _int(g["total_wickets"]),
        "profile_url": _text(g["url"]),
    })
    # the sheet's 'born_on' column actually holds an age, e.g. "(30 years, 5 months yrs)", not a date of birth
    age = _text(g["born_on"]).str.extract(r"(\d+)\s*years?,\s*(\d+)\s*months?")
    p["age_years"] = pd.to_numeric(age[0], errors="coerce").astype("Int64")
    p["age_months"] = pd.to_numeric(age[1], errors="coerce").astype("Int64")
    return p.drop_duplicates("player_id")


def _batting_frame(b):
    return pd.DataFrame({
        "player_id": _int(b["player_id"]),
        "matches": _int(b["Matches"]),
        "innings": _int(b["Innings"]),
        "runs": _int(b["Runs"]),
        "high_score": _int(b["HS"]),
        "average": _num(b["Avg"]),
        "strike_rate": _num(b["SR"]),
        "hundreds": _int(b["100s"]),
        "fifties": _int(b["50s"]),
        "fours": _int(b["4s"]),
        "sixes": _int(b["6s"]),
    })


def _bowling_frame(w):
    return pd.DataFrame({
        "player_id": _int(w["player_id"]),
        "matches": _int(w["Matches"]),
        "innings": _int(w["Innings"]),
        "overs": _num(w["Overs"]),
        "maidens": _int(w["Maidens"]),
        "runs_conceded": _int(w["Runs"]),
        "wickets": _int(w["Wkts"]),
        "average": _num(w["Avg"]),
        "economy": _num(w["Econ"]),
        "strike_rate": _num(w["SR"]),
        "five_wicket_hauls": _int(w["5W"]),
        "ten_wicket_hauls": _int(w["10W"]),
    })


def _split_formats(raw, frame):
    label = raw["Format"].astype("string").str.strip()
    is_all = label.str.lower().eq("alltime")
    career = frame[is_all].drop_duplicates("player_id").reset_index(drop=True)
    by_fmt = frame[~is_all].copy()
    by_fmt.insert(1, "format_label", label[~is_all].values)
    by_fmt.insert(2, "format_group", [format_group(x) for x in by_fmt["format_label"]])
    by_fmt.insert(0, "row_id", range(1, len(by_fmt) + 1))
    return career, by_fmt.reset_index(drop=True)


def build_fielding(f):
    # assisted_wickets / stumped / unassisted_wickets are 0 for every player in the source, so they are dropped
    return pd.DataFrame({
        "player_id": _int(f["player_id"]),
        "catches": _int(f["catches"]),
        "run_outs": _int(f["run_out"]),
    }).drop_duplicates("player_id")


def build_matches(m):
    m = m.copy()
    m["match_id"] = m["match_url"].str.extract(r"/(\d+)/scorecard")[0].astype("Int64")
    m["match_uuid"] = m["match_url"].str.extract(r"match-details/([0-9a-fA-F-]{36})/")[0]
    # player_matches: one row per (player, match)
    pm = m[["player_id", "match_id"]].copy()
    pm["player_id"] = _int(pm["player_id"])
    pm = pm.dropna().drop_duplicates().reset_index(drop=True)

    mm = m.drop_duplicates("match_id").reset_index(drop=True)
    utc = pd.to_datetime(mm["datetime"], format="%d-%m-%Y %I:%M %p", errors="coerce")
    local = pd.Series([_local_datetime_from_url(u) for u in mm["match_url"]], index=mm.index)
    local = local.fillna(utc)
    res = _parse_results(mm.assign(result=_text(mm["result"]), team1=_text(mm["team1"]), team2=_text(mm["team2"])))
    out = pd.DataFrame({
        "match_id": mm["match_id"],
        "match_date": local.dt.strftime("%Y-%m-%d"),
        "match_year": local.dt.year.astype("Int64"),
        "start_time_local": local.dt.strftime("%Y-%m-%d %H:%M"),
        "start_time_utc": utc.dt.strftime("%Y-%m-%d %H:%M"),
        "format": _text(mm["format"]),
        "status": _text(mm["status"]),
        "team1": _text(mm["team1"]),
        "team2": _text(mm["team2"]),
        "result": _text(mm["result"]),
        "winner_name": res["winner_name"],
        "winner_side": res["winner_side"],
        "win_margin": res["win_margin"],
        "win_margin_type": res["win_margin_type"],
        "tournament_stage": _text(mm["tournament_stage"]),
        "match_url": _text(mm["match_url"]),
    })
    # team1_overs/team1_score/team2_overs/team2_score are empty for every row in the source -> dropped
    return out, pm


def build_awards(a):
    out = pd.DataFrame({
        "player_id": _int(a["player_id"]),
        "award_type": _text(a["award_type"]),
        "team": _text(a["team"]),
        "batting_runs": _int(a["batting_runs"]),
        "batting_balls": _int(a["batting_balls"]),
        "batting_fours": _int(a["batting_4s"]),
        "batting_sixes": _int(a["batting_6s"]),
        "batting_strike_rate": _num(a["batting_sr"]),
        "bowling_wickets": _int(a["bowling_wkts"]),
        "bowling_runs_conceded": _int(a["bowling_runs"]),
        "bowling_economy": _num(a["bowling_econ"]),
        "bowling_fours_conceded": _int(a["bowling_4s"]),
        "bowling_sixes_conceded": _int(a["bowling_6s"]),
    })
    out.insert(0, "award_id", range(1, len(out) + 1))
    return out


VIEW_SQL = """
CREATE VIEW player_career AS
SELECT p.player_id, p.name, p.nationality, p.role, p.batting_style, p.bowling_style, p.city,
       bc.matches AS bat_matches, bc.innings AS bat_innings, bc.runs, bc.high_score,
       bc.average AS bat_average, bc.strike_rate AS bat_strike_rate,
       bc.hundreds, bc.fifties, bc.fours, bc.sixes,
       wc.matches AS bowl_matches, wc.overs, wc.runs_conceded, wc.wickets,
       wc.average AS bowl_average, wc.economy, wc.strike_rate AS bowl_strike_rate,
       wc.five_wicket_hauls, wc.ten_wicket_hauls,
       f.catches, f.run_outs
FROM players p
LEFT JOIN batting_career bc ON bc.player_id = p.player_id
LEFT JOIN bowling_career wc ON wc.player_id = p.player_id
LEFT JOIN fielding f        ON f.player_id  = p.player_id;
"""

INDEXES = [
    "CREATE INDEX ix_players_name ON players(name)",
    "CREATE INDEX ix_bbf_player ON batting_by_format(player_id)",
    "CREATE INDEX ix_bbf_group ON batting_by_format(format_group)",
    "CREATE INDEX ix_wbf_player ON bowling_by_format(player_id)",
    "CREATE INDEX ix_wbf_group ON bowling_by_format(format_group)",
    "CREATE INDEX ix_pm_player ON player_matches(player_id)",
    "CREATE INDEX ix_pm_match ON player_matches(match_id)",
    "CREATE INDEX ix_matches_date ON matches(match_date)",
    "CREATE INDEX ix_matches_t1 ON matches(team1)",
    "CREATE INDEX ix_matches_t2 ON matches(team2)",
    "CREATE INDEX ix_awards_player ON awards(player_id)",
]

SCHEMA = {
    "players": "player_id INTEGER PRIMARY KEY, name TEXT, nationality TEXT, role TEXT, batting_style TEXT, bowling_style TEXT, "
               "city TEXT, birth_place TEXT, height_raw TEXT, total_matches INTEGER, total_runs INTEGER, total_wickets INTEGER, "
               "profile_url TEXT, age_years INTEGER, age_months INTEGER",
    "batting_career": "player_id INTEGER PRIMARY KEY, matches INTEGER, innings INTEGER, runs INTEGER, high_score INTEGER, "
                      "average REAL, strike_rate REAL, hundreds INTEGER, fifties INTEGER, fours INTEGER, sixes INTEGER",
    "batting_by_format": "row_id INTEGER PRIMARY KEY, player_id INTEGER, format_label TEXT, format_group TEXT, matches INTEGER, "
                         "innings INTEGER, runs INTEGER, high_score INTEGER, average REAL, strike_rate REAL, hundreds INTEGER, "
                         "fifties INTEGER, fours INTEGER, sixes INTEGER",
    "bowling_career": "player_id INTEGER PRIMARY KEY, matches INTEGER, innings INTEGER, overs REAL, maidens INTEGER, "
                      "runs_conceded INTEGER, wickets INTEGER, average REAL, economy REAL, strike_rate REAL, "
                      "five_wicket_hauls INTEGER, ten_wicket_hauls INTEGER",
    "bowling_by_format": "row_id INTEGER PRIMARY KEY, player_id INTEGER, format_label TEXT, format_group TEXT, matches INTEGER, "
                         "innings INTEGER, overs REAL, maidens INTEGER, runs_conceded INTEGER, wickets INTEGER, average REAL, "
                         "economy REAL, strike_rate REAL, five_wicket_hauls INTEGER, ten_wicket_hauls INTEGER",
    "fielding": "player_id INTEGER PRIMARY KEY, catches INTEGER, run_outs INTEGER",
    "matches": "match_id INTEGER PRIMARY KEY, match_date TEXT, match_year INTEGER, start_time_local TEXT, start_time_utc TEXT, "
               "format TEXT, status TEXT, team1 TEXT, team2 TEXT, result TEXT, winner_name TEXT, winner_side TEXT, "
               "win_margin INTEGER, win_margin_type TEXT, tournament_stage TEXT, match_url TEXT",
    "player_matches": "player_id INTEGER, match_id INTEGER, PRIMARY KEY (player_id, match_id)",
    "awards": "award_id INTEGER PRIMARY KEY, player_id INTEGER, award_type TEXT, team TEXT, batting_runs INTEGER, "
              "batting_balls INTEGER, batting_fours INTEGER, batting_sixes INTEGER, batting_strike_rate REAL, "
              "bowling_wickets INTEGER, bowling_runs_conceded INTEGER, bowling_economy REAL, bowling_fours_conceded INTEGER, "
              "bowling_sixes_conceded INTEGER",
}


def _write(con, name, df):
    con.execute(f"CREATE TABLE {name} ({SCHEMA[name]})")
    cols = list(df.columns)
    df = df.astype(object).where(df.notna(), None)
    rows = [tuple(None if v is pd.NA else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in df.itertuples(index=False, name=None)]
    con.executemany(f"INSERT INTO {name} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)


def import_crickslab(source, db_path, report=None):
    """Read the workbook (path or dict of DataFrames read with dtype=str) and write a fresh database file."""
    if isinstance(source, (str, Path)):
        sheets = pd.read_excel(source, sheet_name=None, dtype=str)
    else:
        sheets = source
    sheets = {_key(k): v for k, v in sheets.items()}
    missing = EXPECTED_SHEETS - set(sheets)
    if missing:
        raise ValueError("Workbook is missing sheet(s): " + ", ".join(sorted(missing)))

    players = build_players(sheets["general_info"])
    bat_c, bat_f = _split_formats(sheets["batting_stats"], _batting_frame(sheets["batting_stats"]))
    bowl_c, bowl_f = _split_formats(sheets["bowling_stats"], _bowling_frame(sheets["bowling_stats"]))
    fielding = build_fielding(sheets["fielding_stats"])
    matches, player_matches = build_matches(sheets["matches"])
    awards = build_awards(sheets["awards"])

    # keep only rows whose player exists (all do in the current data; this guards future files)
    known = set(players["player_id"].dropna())
    tables = {"players": players, "batting_career": bat_c, "batting_by_format": bat_f, "bowling_career": bowl_c,
              "bowling_by_format": bowl_f, "fielding": fielding, "matches": matches,
              "player_matches": player_matches, "awards": awards}
    for n in ("batting_career", "batting_by_format", "bowling_career", "bowling_by_format", "fielding",
              "player_matches", "awards"):
        tables[n] = tables[n][tables[n]["player_id"].isin(known)]

    db_path = Path(db_path)
    tmp = db_path.with_name(db_path.name + ".new")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for name in SCHEMA:
            _write(con, name, tables[name])
        con.executescript(VIEW_SQL)
        for ix in INDEXES:
            con.execute(ix)
        con.commit()
        con.execute("ANALYZE")
        con.commit()
    finally:
        con.close()
    os.replace(tmp, db_path)

    summary = {n: {"rows": int(len(tables[n])), "columns": list(tables[n].columns)} for n in SCHEMA}
    if report is not None:
        report.update(_quality_report(tables))
    return summary


def _quality_report(t):
    m, p = t["matches"], t["players"]
    parsed = m["winner_name"].notna().sum()
    return {
        "matches_total": int(len(m)),
        "matches_with_result": int(m["result"].notna().sum()),
        "results_parsed": int(parsed),
        "winner_side_resolved": int(m["winner_side"].notna().sum()),
        "matches_with_format": int(m["format"].notna().sum()),
        "players_total": int(len(p)),
        "players_with_batting": int(len(t["batting_career"])),
        "players_with_bowling": int(len(t["bowling_career"])),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).with_name("cricket.db"))
    rep = {}
    res = import_crickslab(sys.argv[1], out, rep)
    for t, info in res.items():
        print(f"  {t}: {info['rows']:,} rows")
    print("Quality:", rep)
    print("Done:", out)
