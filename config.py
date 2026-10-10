"""Settings storage. LLM provider, endpoint, API key and model live in config.json."""
import json
import os
from pathlib import Path

BASE = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("CRICKET_CONFIG", BASE / "config.json"))

PROVIDER_DEFAULTS = {
    "anthropic": {"endpoint": "https://api.anthropic.com", "model": "claude-haiku-4-5-20251001"},
    "ollama": {"endpoint": "http://localhost:11434", "model": "llama3.2:3b"},
    "openai": {"endpoint": "http://localhost:1234/v1", "model": ""},
}

DEFAULT_CONFIG = {
    "provider": "ollama",
    "summarize": True,
    "num_ctx": 8192,
    "providers": {p: {**d, "api_key": ""} for p, d in PROVIDER_DEFAULTS.items()},
}


def load_file():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
        for k in ("provider", "summarize", "num_ctx"):
            if k in saved:
                cfg[k] = saved[k]
        for p, v in (saved.get("providers") or {}).items():
            if p in cfg["providers"] and isinstance(v, dict):
                for k in ("endpoint", "model", "api_key"):
                    if k in v:
                        cfg["providers"][p][k] = v[k]
    return cfg


def load():
    """Saved settings plus ANTHROPIC_API_KEY from the environment (if no key is saved)."""
    cfg = load_file()
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key and not cfg["providers"]["anthropic"]["api_key"]:
        cfg["providers"]["anthropic"]["api_key"] = env_key
    return cfg


def save(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)  # key is readable by this user only (no-op on Windows)
    except Exception:
        pass


def update(payload):
    cfg = load_file()
    if payload.get("provider") in PROVIDER_DEFAULTS:
        cfg["provider"] = payload["provider"]
    if "summarize" in payload:
        cfg["summarize"] = bool(payload["summarize"])
    if "num_ctx" in payload:
        try:
            cfg["num_ctx"] = max(1024, min(int(payload["num_ctx"]), 131072))
        except (TypeError, ValueError):
            pass
    for p, v in (payload.get("providers") or {}).items():
        if p not in cfg["providers"] or not isinstance(v, dict):
            continue
        tgt = cfg["providers"][p]
        if "endpoint" in v:
            tgt["endpoint"] = str(v["endpoint"]).strip()
        if "model" in v:
            tgt["model"] = str(v["model"]).strip()
        key = v.get("api_key")
        if key == "__CLEAR__":
            tgt["api_key"] = ""
        elif key:
            tgt["api_key"] = str(key).strip()
    save(cfg)
    return cfg


def public(cfg):
    """Config for the browser: never includes API keys, only whether one is saved."""
    return {
        "provider": cfg["provider"],
        "summarize": cfg["summarize"],
        "num_ctx": cfg["num_ctx"],
        "providers": {
            p: {"endpoint": v["endpoint"], "model": v["model"], "has_key": bool(v["api_key"])}
            for p, v in cfg["providers"].items()
        },
    }


def resolve(override=None):
    """Settings actually used for an LLM call. `override` (from the Test button) wins over saved values."""
    cfg = load()
    o = override or {}
    p = o.get("provider") if o.get("provider") in PROVIDER_DEFAULTS else cfg["provider"]
    base = cfg["providers"][p]
    try:
        num_ctx = int(o.get("num_ctx") or cfg["num_ctx"])
    except (TypeError, ValueError):
        num_ctx = cfg["num_ctx"]
    return {
        "provider": p,
        "endpoint": (o.get("endpoint") or base["endpoint"]).strip(),
        "api_key": (o.get("api_key") or base["api_key"]).strip(),
        "model": (o.get("model") or base["model"]).strip(),
        "num_ctx": num_ctx,
    }
