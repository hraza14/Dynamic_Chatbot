# Cricket Data Chatbot

Ask questions about your cricket data in plain English. The app turns each question into a SQL query, runs it on a
local SQLite file (`cricket.db`) and shows the answer as a table, with the SQL visible so every answer can be checked.

The LLM is your choice and is set in the app's **Settings**: Claude (API key), Ollama (local), or any
OpenAI-compatible endpoint. You can switch at any time.

> The included `cricket.db` is built from **`crickslab_players.xlsx`** (9,999 players, 16,261 matches). To rebuild it
> or load an updated export, see "Loading your data".

## 1. Requirements

* **Python 3.9 or newer** (python.org). That is the only thing to install.
* **SQLite needs no download.** It is built into Python; the whole database is the single file `cricket.db`.
  MySQL is not used and does not need to be installed.
* An LLM, either a Claude API key **or** Ollama installed locally (see section 3).

## 2. Run it

* Windows: double-click `run.bat`
* Mac/Linux: `./run.sh`

The first run creates a `.venv` and installs the packages (needs internet once). The browser opens at
http://127.0.0.1:8000. Manual alternative: `pip install -r requirements.txt` then `python app.py`.

## 3. Connect an LLM (Settings button, top right)

Pick a provider, fill in endpoint / API key / model, press **Test connection**, then **Save**.
Test connection makes a real request, so a green tick means the credentials and model name work.

### Claude (Anthropic API)
1. Create an API key at https://console.anthropic.com (API keys). Billing is pay-per-use on your Anthropic account.
2. Provider: *Claude*. Endpoint: `https://api.anthropic.com` (default). Paste the key. Model, e.g.
   `claude-haiku-4-5-20251001` (fast, cheap, default) or `claude-sonnet-5-5` (stronger).
3. Test connection, Save.
   Alternatively set the environment variable `ANTHROPIC_API_KEY` before starting the app.

### Ollama (free, fully local)
1. Install Ollama from https://ollama.com and make sure it is running.
2. Download a model once, e.g. `ollama pull llama3.2:3b` (smaller: `llama3.2:1b`; others to try:
   `qwen2.5-coder:1.5b`, `qwen2.5-coder:7b`, `gemma2:2b`).
3. Provider: *Ollama*. Endpoint: `http://localhost:11434`. API key: leave empty. Model: the exact name from
   `ollama list`. Test connection, Save.
4. If Ollama runs on another machine, use that machine's address as the endpoint (and start it with
   `OLLAMA_HOST=0.0.0.0`).

**About 1B-2B models:** they work for simple questions ("top 10 run scorers", "list the teams") but often get
joins and averages wrong. If answers look wrong, try a 7B model or Claude. The schema and data dictionary (about 3,500 tokens) are sent with every question, so keep *Context size* at 8192 or
higher in Settings (the default). Ollama silently cuts the start of a prompt that does not fit, which makes answers poor.

### Any OpenAI-compatible server
LM Studio (`http://localhost:1234/v1`), Ollama's compatibility mode (`http://localhost:11434/v1`), Groq, OpenRouter,
vLLM, etc. Provider: *OpenAI-compatible*, enter the endpoint, key (if the service needs one) and model name.

API keys are stored in `config.json` next to the app, on this computer only. **Do not send `config.json` to anyone.**

## 4. Loading your data

The database shipped in this folder is already built from `crickslab_players.xlsx`. To rebuild it, or to load a newer export
of the same workbook: click **Data → choose file → Import** in the app, or run
`python import_data.py crickslab_players.xlsx` (replaces `cricket.db`).

The app recognises the Crickslab workbook (sheets *General Info, Batting Stats, Bowling Stats, Fielding Stats, Matches, Awards*)
and applies a dedicated loader (`crickslab_etl.py`). Any other Excel/CSV file is loaded generically, one table per sheet
(column names in row 1, converted to lower_snake_case).

### How the Crickslab workbook is modelled

The raw sheets store every number as text, use `--` for "missing", and are not one-row-per-thing, so they are remodelled:

| Table | One row per | Built from |
|---|---|---|
| `players` | player (9,999) | General Info. Missing values are NULL; `born_on` actually held an *age*, now `age_years`/`age_months`. |
| `batting_career`, `bowling_career` | player | the **AllTime** row of Batting / Bowling Stats |
| `batting_by_format`, `bowling_by_format` | player x competition | all other rows. `format_label` is the raw competition name (about 550 variants); `format_group` collapses it to T20, T10, ODI, T30, T40, Test / multi-day or Other. |
| `fielding` | player | catches, run_outs (3 columns that were 0 for everyone were dropped) |
| `matches` | **match** (16,261) | the Matches sheet had one row per *player appearance*; matches are de-duplicated by the id in the scorecard URL. Result text is split into `winner_name`, `win_margin`, `win_margin_type`. |
| `player_matches` | player x match (59,501) | who played in which match |
| `awards` | award (29,719) | Awards sheet, typed |
| `player_career` (view) | player | players + career batting + bowling + fielding in one place; the easiest table for most questions |

Things worth knowing about the source data (also written into `data_dictionary.txt` so the chatbot knows them):

* Per-format rows do **not** sum to the AllTime row (they match for about 81% of players), so they are kept in separate tables.
* Match times in the sheet are **UTC**. `matches.match_date` / `start_time_local` come from the local time in the scorecard URL.
* `team1_score`, `team1_overs`, `team2_score`, `team2_overs` were empty for every row, so there are no team scores.
* `matches.format` is blank for about 23% of matches. Player names are not unique (about 8,700 names for 9,999 players).
* `winner_side` (which of team1/team2 won) could be resolved for about 90% of results, because winners are often written as
  abbreviations ("KK won by 7 runs"). `winner_name` is always available when a result exists.
* Matches are not linked to teams or players' teams (no team table exists in the source); `awards.team` is the only player-to-team link.

`data_dictionary.txt` is sent with every question. Edit it to teach the chatbot your own terminology.

## 5. How it works and its limits

* Each question goes to the LLM together with the table/column list, example rows and short value lists.
* Only a single `SELECT` is ever executed, on a read-only connection with a 15-second limit. If the SQL fails, the
  error is sent back to the model once for a retry.
* Results show at most 200 rows. There are no charts.
* **LLMs can write wrong SQL that still runs.** Use "Show SQL" to check important answers.
* The server listens on `127.0.0.1` only (this computer). Set `HOST=0.0.0.0` to share it on your network; there is no
  login, so only do that on a trusted network.

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot connect to http://localhost:11434" | Ollama isn't running. Start it, then Test again. |
| "model ... not found" | Run `ollama pull <model>` or pick a name from the dropdown after Test. |
| Claude "401 / API key is invalid" | Re-paste the key; check it belongs to an account with credit. |
| First Ollama answer is very slow | The model is loading into memory; later answers are faster. |
| "No data loaded yet" | Use Data → Import. |
| Wrong or odd answers | Add notes to `data_dictionary.txt`; use a larger model; check "Show SQL". |

## 7. Self-test (no API key or Ollama needed)

`python dev/selftest.py` starts the app together with a mock LLM server and checks the Claude, Ollama and
OpenAI-compatible request formats, retries, SQL safety and Excel upload (including a Crickslab-shaped workbook).
Note it tests the app's plumbing, not how well a particular model writes SQL; try your real questions with your chosen model.

## Files

`app.py` web server and API · `agent.py` question → SQL → answer · `llm.py` Claude/Ollama/OpenAI adapters ·
`db.py` SQLite + safety · `import_data.py` Excel/CSV loader · `crickslab_etl.py` the Crickslab workbook cleaner/modeller ·
`data_dictionary.txt` plain-English notes for the model · `static/index.html` the interface · `dev/` test tools
