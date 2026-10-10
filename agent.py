"""Question -> SQL -> rows -> short answer."""
import json
import re
import sqlite3

import db
import llm

RULES = """You are a careful data analyst. You answer questions about a cricket database by writing SQLite queries.

Rules:
1. Reply with exactly ONE SQLite SELECT statement and nothing else. No explanation, no markdown.
2. Use only the tables and columns shown in the schema. Never invent names.
3. Join tables through their id columns. Compare text case-insensitively, e.g. LOWER(col) = LOWER('value') or col LIKE '%value%'.
4. For 'top', 'most', 'best' or 'highest' questions, ORDER BY the measure DESC and LIMIT 10 unless the user gives a number.
5. Give result columns short readable aliases. Round averages to 2 decimals.
6. If the question cannot be answered from this schema, reply: SELECT 'This cannot be answered from the available data.' AS message
"""

SUMMARY_SYSTEM = ("You explain database query results to a cricket analyst. Write 1-3 short sentences using ONLY the "
                  "names and numbers in the result. Do not add facts. Do not mention SQL or tables.")


def build_system(db_path):
    return RULES + "\nDATABASE SCHEMA\n" + db.schema_text(db_path)


def extract_sql(text):
    t = (text or "").strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    m = re.search(r"^\s*(select|with)\b", t, re.I | re.M) or re.search(r"\b(select|with)\b", t, re.I)
    if m:
        t = t[m.start():]
    return t.split(";")[0].strip()


def _history_messages(history):
    msgs = []
    for h in (history or [])[-3:]:
        if isinstance(h, dict) and isinstance(h.get("question"), str) and isinstance(h.get("sql"), str) \
                and h["question"].strip() and h["sql"].strip():
            msgs += [{"role": "user", "content": h["question"]}, {"role": "assistant", "content": h["sql"]}]
    return msgs


def _summarize(s, question, cols, rows, total):
    shown = rows[:20]
    payload = json.dumps([dict(zip(cols, r)) for r in shown], ensure_ascii=False, default=str)
    msg = f"Question: {question}\nResult ({len(shown)} of {total} rows shown):\n{payload}"
    return llm.chat(s, SUMMARY_SYSTEM, [{"role": "user", "content": msg}], max_tokens=200)


def ask(question, history, s, db_path, summarize=True, max_rows=200):
    out = {"question": question, "sql": None, "columns": [], "rows": [], "row_count": 0,
           "truncated": False, "answer": "", "error": None, "attempts": 0}
    system = build_system(db_path)
    msgs = _history_messages(history) + [{"role": "user", "content": question}]
    last_err = None
    for attempt in range(2):  # one automatic retry, feeding the SQL error back to the model
        out["attempts"] = attempt + 1
        raw = llm.chat(s, system, msgs, max_tokens=500)
        out["sql"] = extract_sql(raw)
        try:
            cols, rows, truncated = db.run_query(out["sql"], db_path, max_rows)
            break
        except (ValueError, sqlite3.Error) as e:
            last_err = str(e)
            msgs = msgs + [
                {"role": "assistant", "content": raw or "(empty reply)"},
                {"role": "user", "content": f"That SQL failed: {e}\nReturn a corrected single SELECT statement only."},
            ]
    else:
        out["error"] = f"The model's SQL could not be run: {last_err}"
        return out

    out.update(columns=cols, rows=rows, row_count=len(rows), truncated=truncated)
    if not rows:
        out["answer"] = "No rows matched your question."
        return out
    out["answer"] = f"{len(rows)}{'+' if truncated else ''} row(s) found."
    if summarize:
        try:
            text = _summarize(s, question, cols, rows, len(rows))
            if text:
                out["answer"] = text
        except llm.LLMError:
            pass  # the table is the real answer; the sentence is a bonus
    return out
