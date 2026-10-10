"""Fake LLM server for testing the app without any real model or API key.
Speaks the Ollama (/api/chat), Anthropic (/v1/messages) and OpenAI (/v1/chat/completions) wire formats and
rejects requests that are malformed in the ways the real services would reject them.
Run:  python dev/mock_llm.py      (listens on http://127.0.0.1:9100)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOOD_KEY = "test-key"
SQL = {
    "most runs": "SELECT player_id, name, runs FROM player_career WHERE runs IS NOT NULL ORDER BY runs DESC LIMIT 10",
    "teams": "SELECT DISTINCT team1 AS team FROM matches ORDER BY team LIMIT 8",
    "fenced": "```sql\nSELECT COUNT(*) AS matches_played FROM matches;\n```\nThis counts the matches.",
    "drop": "DROP TABLE players",
    "multi": "SELECT 1; DELETE FROM players",
}


def pick_sql(question, history_text):
    q = question.lower()
    if "retry" in q:
        if "That SQL failed" in history_text:
            return SQL["teams"]
        return "SELECT nonexistent_col FROM nowhere"
    for k, v in SQL.items():
        if k in q:
            return v
    return "SELECT 'This cannot be answered from the available data.' AS message"


def answer(system, messages):
    last_user = [m for m in messages if m["role"] == "user"][-1]["content"]
    if "connectivity test" in system:
        return "OK"
    if "explain database query results" in system:
        return "Mock summary of the result."
    return pick_sql(messages[0]["content"] if "That SQL failed" in last_user else last_user,
                    "\n".join(m["content"] for m in messages))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": "mock-1b:latest"}, {"name": "mock-2b:latest"}]})
        if self.path == "/v1/models":
            if self.headers.get("x-api-key") == GOOD_KEY or self.headers.get("Authorization") == f"Bearer {GOOD_KEY}":
                return self._send(200, {"data": [{"id": "mock-model"}]})
            return self._send(401, {"error": {"message": "invalid key"}})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs = body.get("messages", [])
        if self.path == "/api/chat":  # Ollama
            if body.get("stream") is not False or not body.get("model"):
                return self._send(400, {"error": "bad request"})
            if body["model"] not in ("mock-1b:latest", "mock-1b", "mock-2b:latest"):
                return self._send(404, {"error": f"model '{body['model']}' not found"})
            sys_msg = msgs[0]
            assert sys_msg["role"] == "system"
            return self._send(200, {"message": {"role": "assistant", "content": answer(sys_msg["content"], msgs[1:])}})
        if self.path == "/v1/messages":  # Anthropic
            if self.headers.get("x-api-key") != GOOD_KEY:
                return self._send(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
            if not self.headers.get("anthropic-version") or "max_tokens" not in body or not isinstance(body.get("system"), str):
                return self._send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "missing field"}})
            roles = [m["role"] for m in msgs]
            if roles[0] != "user" or any(roles[i] == roles[i + 1] for i in range(len(roles) - 1)) \
                    or any(not m["content"] for m in msgs):
                return self._send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad message order"}})
            return self._send(200, {"type": "message", "role": "assistant",
                                    "content": [{"type": "text", "text": answer(body["system"], msgs)}]})
        if self.path == "/v1/chat/completions":  # OpenAI-compatible
            if self.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
                return self._send(401, {"error": {"message": "Incorrect API key"}})
            return self._send(200, {"choices": [{"message": {"role": "assistant",
                                                             "content": answer(msgs[0]["content"], msgs[1:])}}]})
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
    print(f"Mock LLM on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
