"""Cricket data chatbot - local web app.   Run:  python app.py   then open http://127.0.0.1:8000"""
import os
import shutil
import tempfile
import threading
import traceback
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import agent
import config
import db
import import_data
import llm

app = FastAPI(title="Cricket Data Chatbot")
STATIC = Path(__file__).parent / "static"


class ChatReq(BaseModel):
    question: str
    history: list = []


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {**config.public(config.load()), "defaults": config.PROVIDER_DEFAULTS}


@app.post("/api/config")
def set_config(payload: dict):
    config.update(payload)
    return {**config.public(config.load()), "defaults": config.PROVIDER_DEFAULTS}


@app.post("/api/llm/test")
def llm_test(payload: dict):
    s = config.resolve(payload)
    try:
        return llm.test_connection(s)
    except llm.LLMError as e:
        return {"ok": False, "error": str(e), "models": llm.list_models(s)}


@app.get("/api/schema")
def schema():
    return {"exists": db.exists(db.DB_PATH), "path": str(db.DB_PATH), "tables": db.get_schema(db.DB_PATH)}


@app.post("/api/chat")
def chat(req: ChatReq):
    q = req.question.strip()
    if not q:
        return {"error": "Please type a question."}
    if not db.exists(db.DB_PATH):
        return {"question": q, "error": "No data loaded yet. Click Data and upload your Excel file."}
    cfg = config.load()
    try:
        return agent.ask(q, req.history, config.resolve(), db.DB_PATH, cfg["summarize"])
    except llm.LLMError as e:
        return {"question": q, "error": str(e)}
    except Exception as e:  # keep the server alive, show a readable message
        traceback.print_exc()
        return {"question": q, "error": f"Unexpected error: {e}"}


@app.post("/api/upload")
def upload(file: UploadFile = File(...), replace: bool = Form(True)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in import_data.SUPPORTED:
        return JSONResponse({"error": "Please upload an .xlsx or .csv file."}, status_code=400)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ("upload" + suffix)
        with open(p, "wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            summary = import_data.import_file(p, db.DB_PATH, fresh=replace, table_hint=Path(file.filename).stem)
        except Exception as e:
            return JSONResponse({"error": f"Import failed: {e}"}, status_code=400)
    return {"imported": summary}


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    url = f"http://{host}:{port}"
    print(f"Cricket Data Chatbot running at {url}   (Ctrl+C to stop)")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
