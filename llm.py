"""LLM provider adapters. Three kinds, all driven by endpoint + API key + model:

  anthropic - Claude via the Anthropic Messages API
  ollama    - local Ollama server (/api/chat)
  openai    - any OpenAI-compatible server (LM Studio, vLLM, Groq, OpenRouter, Ollama's /v1, ...)
"""
import json
import re
import time

import requests

ANTHROPIC_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


def _bearer(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


def _err_text(r):
    try:
        j = r.json()
        e = j.get("error", j) if isinstance(j, dict) else j
        if isinstance(e, dict):
            e = e.get("message") or json.dumps(e)
        return str(e)[:400]
    except Exception:
        return r.text[:400]


def _post(url, headers, body, timeout):
    try:
        return requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise LLMError(f"Cannot connect to {url}. Check the endpoint, and that the server is running.")
    except requests.exceptions.Timeout:
        raise LLMError(f"Timed out waiting for {url}. A local model may still be loading - try again.")
    except requests.exceptions.RequestException as e:
        raise LLMError(f"Request failed: {e}")


def _strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def chat(s, system, messages, max_tokens=600):
    """Send one chat request. `messages` = [{'role': 'user'|'assistant', 'content': str}, ...]."""
    p, ep, model, key = s["provider"], s["endpoint"].rstrip("/"), s["model"], s["api_key"]
    if not ep:
        raise LLMError("No endpoint is set. Open Settings and enter one.")
    if not model:
        raise LLMError("No model name is set. Open Settings and enter one.")

    if p == "anthropic":
        if not key:
            raise LLMError("Anthropic API key is missing. Open Settings and paste it in.")
        url = ep + "/messages" if ep.endswith("/v1") else ep + "/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0, "system": system, "messages": messages}
        r = _post(url, headers, body, 120)
        if r.status_code == 400 and "temperature" in r.text.lower():
            body.pop("temperature")
            r = _post(url, headers, body, 120)
        if r.status_code != 200:
            raise LLMError(f"Anthropic API error {r.status_code}: {_err_text(r)}")
        parts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
        return _strip_thinking("".join(parts))

    if p == "ollama":
        base = ep[:-4] if ep.endswith("/api") else ep
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": s.get("num_ctx", 8192), "num_predict": max_tokens},
        }
        r = _post(base + "/api/chat", _bearer(key), body, 300)
        if r.status_code == 404:
            raise LLMError(f"Ollama says: {_err_text(r)}. If the model is missing, run:  ollama pull {model}")
        if r.status_code != 200:
            raise LLMError(f"Ollama error {r.status_code}: {_err_text(r)}")
        return _strip_thinking((r.json().get("message") or {}).get("content", ""))

    if p == "openai":
        url = ep if ep.endswith("/chat/completions") else ep + "/chat/completions"
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}] + messages}
        r = _post(url, _bearer(key), body, 180)
        if r.status_code != 200:
            raise LLMError(f"API error {r.status_code}: {_err_text(r)}")
        choices = r.json().get("choices") or [{}]
        return _strip_thinking((choices[0].get("message") or {}).get("content", ""))

    raise LLMError(f"Unknown provider: {p}")


def list_models(s):
    """Best-effort list of model names the server offers (empty list if unavailable)."""
    p, ep, key = s["provider"], s["endpoint"].rstrip("/"), s["api_key"]
    try:
        if p == "ollama":
            base = ep[:-4] if ep.endswith("/api") else ep
            r = requests.get(base + "/api/tags", headers=_bearer(key), timeout=6)
            return sorted(m["name"] for m in r.json().get("models", [])) if r.ok else []
        if p == "anthropic":
            if not key:
                return []
            url = ep + "/models" if ep.endswith("/v1") else ep + "/v1/models"
            r = requests.get(url, headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}, timeout=6)
        else:
            r = requests.get(ep + "/models", headers=_bearer(key), timeout=6)
        return [m["id"] for m in r.json().get("data", [])] if r.ok else []
    except Exception:
        return []


def test_connection(s):
    """Real round trip: list models, then ask the model for one word. Raises LLMError on any problem."""
    t = time.time()
    models = list_models(s)
    reply = chat(s, "You are a connectivity test.", [{"role": "user", "content": "Reply with the single word OK."}],
                 max_tokens=20)
    return {"ok": True, "reply": reply[:80], "models": models, "seconds": round(time.time() - t, 1)}
