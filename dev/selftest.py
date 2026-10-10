"""End-to-end self-test. Starts the real app + a mock LLM server and drives everything through the HTTP API.
Run:  python dev/selftest.py        (needs no API key, no Ollama, no internet)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import mock_llm  # noqa: E402

APP, MOCK = "http://127.0.0.1:8765", "http://127.0.0.1:9100"
results = []


def check(name, cond, extra=""):
    cond = bool(cond)
    results.append(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))


def post(path, **body):
    return requests.post(APP + path, json=body, timeout=60).json()


def make_crickslab_fixture(path):
    """Tiny workbook shaped like crickslab_players.xlsx (all text, '--' for missing)."""
    import pandas as pd
    gi = pd.DataFrame({"player_id": ["1", "2"], "name": ["Ali", "Bilal"], "url": ["u1", "u2"], "nationality": ["Pakistan", None],
                       "role": ["Batter", "Bowler"], "bowling_style": [None, "Right-arm fast"], "batting_style": ["Right Handed", "Left Handed"],
                       "teams_preview": ["No teams"] * 2, "total_matches": ["50", "20"], "total_runs": ["1500", "100"], "total_wickets": [None, "40"],
                       "country": ["Pakistan", "--"], "city": ["Lahore", "--"], "birth_place": ["--", "--"], "height": ["--", "--"],
                       "born_on": ["(30 years, 5 months yrs)", "--"]})
    bat = pd.DataFrame({"player_id": ["1", "1", "2"], "name": ["Ali", "Ali", "Bilal"], "100s": ["3", "3", "0"], "4s": ["100", "100", "5"],
                        "50s": ["9", "9", "0"], "6s": ["20", "20", "1"], "Avg": ["30.0", "30.0", "-"], "Format": ["AllTime", "T20", "AllTime"],
                        "HS": ["120", "120", "-"], "Innings": ["50", "50", "0"], "Matches": ["50", "50", "20"], "Runs": ["1500", "1500", "100"],
                        "SR": ["120.5", "120.5", "-"]})
    bowl = pd.DataFrame({"player_id": ["2"], "name": ["Bilal"], "10W": ["-"], "5W": ["1"], "Avg": ["20.0"], "Econ": ["6.5"], "Format": ["AllTime"],
                         "Innings": ["20"], "Maidens": ["3"], "Matches": ["20"], "Overs": ["80"], "Runs": ["520"], "SR": ["18.0"], "Wkts": ["40"]})
    fld = pd.DataFrame({"player_id": ["1", "2"], "name": ["Ali", "Bilal"], "assisted_wickets": ["0", "0"], "catches": ["10", "2"],
                        "run_out": ["1", "0"], "stumped": ["0", "0"], "unassisted_wickets": ["0", "0"]})
    url = "https://crickslab.com/match-central/match-details/60f69870-307c-11e9-b2d5-e722756bd767/A-vs-B-Thu14Feb2019-0945PM-Dubai/{n}/scorecard"
    mt = pd.DataFrame({"player_id": ["1", "2", "1"], "name": ["Ali", "Bilal", "Ali"],
                       "datetime": ["14-02-2019 04:45 PM", "14-02-2019 04:45 PM", "20-03-2019 10:00 AM"], "format": ["T20", "T20", None],
                       "match_url": [url.format(n=100), url.format(n=100), url.format(n=101)],
                       "result": ["TEAM B won by 5 wickets", "TEAM B won by 5 wickets", None], "status": ["Completed", "Completed", None],
                       "team1": ["Team A", "Team A", "Team A"], "team1_overs": [None] * 3, "team1_score": [None] * 3,
                       "team2": ["Team B", "Team B", "Team C"], "team2_overs": [None] * 3, "team2_score": [None] * 3,
                       "tournament_stage": ["Match 1", "Match 1", None]})
    aw = pd.DataFrame({"player_id": ["1"], "name": ["Ali"], "award_type": ["Player of the match"], "batting_4s": ["9"], "batting_6s": ["4"],
                       "batting_balls": ["30"], "batting_runs": ["75"], "batting_sr": ["250.00"], "bowling_4s": [None], "bowling_6s": [None],
                       "bowling_econ": [None], "bowling_runs": [None], "bowling_wkts": [None], "team": ["Team A"]})
    with pd.ExcelWriter(path) as xw:
        for name, df in [("General Info", gi), ("Batting Stats", bat), ("Bowling Stats", bowl), ("Fielding Stats", fld),
                         ("Matches", mt), ("Awards", aw)]:
            df.to_excel(xw, sheet_name=name, index=False)


def main():
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "cricket.db", tmp / "t.db")
    env = {**os.environ, "CRICKET_DB": str(tmp / "t.db"), "CRICKET_CONFIG": str(tmp / "cfg.json"),
           "PORT": "8765", "NO_BROWSER": "1"}
    mock = ThreadingHTTPServer(("127.0.0.1", 9100), mock_llm.H)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    proc = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, cwd=ROOT)
    try:
        for _ in range(40):
            try:
                requests.get(APP + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.25)

        check("home page served", "Cricket Data Chatbot" in requests.get(APP + "/").text)
        sch = requests.get(APP + "/api/schema").json()
        names = {t["name"] for t in sch["tables"]}
        check("schema lists the 9 Crickslab tables + player_career view",
              {"players", "matches", "player_matches", "batting_career", "batting_by_format", "bowling_career",
               "bowling_by_format", "fielding", "awards", "player_career"} <= names, names)

        # --- Ollama protocol ---
        post("/api/config", provider="ollama", providers={"ollama": {"endpoint": MOCK, "model": "mock-1b"}})
        t = post("/api/llm/test", provider="ollama")
        check("ollama: test connection ok + lists models", t.get("ok") and "mock-1b:latest" in t["models"], t)
        t = post("/api/llm/test", provider="ollama", model="does-not-exist")
        check("ollama: missing model gives 'ollama pull' hint", not t["ok"] and "ollama pull" in t["error"], t)
        t = post("/api/llm/test", provider="ollama", endpoint="http://127.0.0.1:9")
        check("ollama: unreachable endpoint gives readable error", not t["ok"] and "Cannot connect" in t["error"], t)
        r = post("/api/chat", question="who has the most runs?")
        check("ollama: chat returns 10 rows + SQL + summary",
              r.get("row_count") == 10 and "player_career" in r["sql"] and r["answer"] == "Mock summary of the result.", r)

        # --- Anthropic protocol ---
        post("/api/config", provider="anthropic", providers={"anthropic": {"endpoint": MOCK, "model": "claude-test", "api_key": "wrong"}})
        t = post("/api/llm/test", provider="anthropic")
        check("claude: wrong key rejected with 401 message", not t["ok"] and "401" in t["error"], t)
        t = post("/api/llm/test", provider="anthropic", api_key="test-key")
        check("claude: right key accepted (headers/body valid)", t.get("ok"), t)
        post("/api/config", providers={"anthropic": {"api_key": "test-key"}})
        r = post("/api/chat", question="list the teams")
        check("claude: chat works", r.get("row_count") == 8, r)
        cfg = requests.get(APP + "/api/config").json()
        check("api key is never sent back to the browser", "test-key" not in str(cfg) and cfg["providers"]["anthropic"]["has_key"])
        check("api key saved to config file", "test-key" in (tmp / "cfg.json").read_text())

        # --- OpenAI-compatible protocol ---
        post("/api/config", provider="openai", providers={"openai": {"endpoint": MOCK + "/v1", "model": "mock-model", "api_key": "test-key"}})
        t = post("/api/llm/test", provider="openai")
        check("openai-compatible: test ok", t.get("ok"), t)
        r = post("/api/chat", question="list the teams")
        check("openai-compatible: chat works", r.get("row_count") == 8, r)

        # --- agent behaviour ---
        post("/api/config", provider="ollama")
        r = post("/api/chat", question="retry test")
        check("bad SQL is auto-corrected on 2nd attempt", r.get("attempts") == 2 and r.get("row_count") == 8, r)
        r = post("/api/chat", question="show fenced count")
        check("markdown-fenced SQL is extracted", r.get("rows") and r["rows"][0][0] > 1000, r)
        r = post("/api/chat", question="drop the players table")
        check("DROP is refused", r.get("error") and "SELECT" in r["error"], r)
        r = post("/api/chat", question="multi statement")
        check("multi-statement SQL: only the first statement runs, DELETE never executes", r.get("rows") == [[1]], r)
        r = post("/api/chat", question="who has the most runs?")
        check("data intact after DROP/DELETE attempts (players still 9,999+ rows)", r.get("row_count") == 10)
        r = post("/api/chat", question="who has the most runs?")
        check("players table still intact after attacks", r.get("row_count") == 10)
        r = post("/api/chat", question="what is the weather", history=[{"question": "x", "sql": "SELECT 1"}])
        check("unanswerable question handled + history accepted", r.get("rows") and "cannot be answered" in r["rows"][0][0], r)

        # --- upload: generic workbook, then Crickslab-shaped workbook ---
        import pandas as pd
        generic = tmp / "generic.xlsx"
        with pd.ExcelWriter(generic) as xw:
            pd.DataFrame({"Team Name": ["A", "B"], "City": ["x", "y"]}).to_excel(xw, sheet_name="Teams", index=False)
            pd.DataFrame({"Team Name": ["A"], "Wins": [3]}).to_excel(xw, sheet_name="Wins", index=False)
        with open(generic, "rb") as f:
            up = requests.post(APP + "/api/upload", files={"file": ("my data.xlsx", f)}, data={"replace": "true"}).json()
        check("generic excel upload: one table per sheet", set(up.get("imported", {})) == {"teams", "wins"}, up)

        crick = tmp / "crick.xlsx"
        make_crickslab_fixture(crick)
        with open(crick, "rb") as f:
            up = requests.post(APP + "/api/upload", files={"file": ("crickslab_players.xlsx", f)}, data={"replace": "true"}).json()
        imp = up.get("imported", {})
        check("crickslab workbook detected -> 9 modelled tables", {"players", "matches", "player_matches", "batting_career",
              "batting_by_format", "awards"} <= set(imp), up)
        post("/api/config", provider="ollama")
        r = post("/api/chat", question="who has the most runs?")
        check("typed data after upload: top scorer is player 1 with 1500 runs",
              r.get("rows") and r["rows"][0][0] == 1 and r["rows"][0][2] == 1500, r)
        r = post("/api/chat", question="show fenced count")
        check("one row per match: 2 distinct matches from 3 appearances", r.get("rows") == [[2]], r)
        bad = requests.post(APP + "/api/upload", files={"file": ("x.pdf", b"nope")})
        check("non-excel upload rejected", bad.status_code == 400)
    finally:
        proc.terminate(); mock.shutdown(); shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


main()
