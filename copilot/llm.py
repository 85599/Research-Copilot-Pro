"""Provider-agnostic chat client over plain REST (no vendor SDKs). One method: invoke(system, user)."""
import os
import threading
import time

import requests

from .config import PROVIDERS

RETRY = (408, 409, 429, 500, 502, 503, 504, 529)


class LLMError(RuntimeError):
    pass


class Usage:
    """Thread-safe token/call counter, shown in the UI like TradingAgents' CLI stats."""

    def __init__(self):
        self.calls = self.tin = self.tout = 0
        self._l = threading.Lock()

    def add(self, tin, tout):
        with self._l:
            self.calls += 1
            self.tin += tin or 0
            self.tout += tout or 0

    def as_dict(self):
        return {"calls": self.calls, "tokens_in": self.tin, "tokens_out": self.tout}


class LLM:
    def __init__(self, provider, model, cfg, usage=None):
        if provider not in PROVIDERS:
            raise LLMError(f"Unknown provider {provider!r}")
        p = PROVIDERS[provider]
        if not model:
            raise LLMError(f"No model set for {p['label']}. Type a model id in the form.")
        self.provider, self.kind, self.model = provider, p["kind"], model
        self.url = (cfg.get("backend_url") or p["url"]).rstrip("/")
        self.key = os.environ.get(p["key_env"], "") if p["key_env"] else ""
        if p["key_env"] and not self.key and provider != "openai_compatible":
            raise LLMError(f"{p['key_env']} is not set. Add it to the .env file and restart the server.")
        self.temp, self.max_tokens, self.retries = cfg.get("temperature"), cfg.get("max_tokens") or 4096, cfg.get("llm_max_retries") or 3
        self.usage = usage or Usage()

    # ---- wire formats -------------------------------------------------
    def _request(self, system, user, max_tokens):
        if self.kind == "anthropic":
            body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            if self.temp is not None:
                body["temperature"] = self.temp
            return (self.url + "/v1/messages", {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body, {})
        if self.kind == "google":
            body = {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"maxOutputTokens": max(max_tokens, 8192)}}
            if self.temp is not None:
                body["generationConfig"]["temperature"] = self.temp
            return (f"{self.url}/models/{self.model}:generateContent", {"x-goog-api-key": self.key}, body, {})
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        body["max_completion_tokens" if self.provider == "openai" else "max_tokens"] = max_tokens
        if self.temp is not None:
            body["temperature"] = self.temp
        hdr = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        return (self.url + "/chat/completions", hdr, body, {})

    def _parse(self, j):
        if self.kind == "anthropic":
            text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
            u = j.get("usage", {})
            return text, u.get("input_tokens"), u.get("output_tokens")
        if self.kind == "google":
            parts = (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
            u = j.get("usageMetadata", {})
            return "".join(p.get("text", "") for p in parts if not p.get("thought")), u.get("promptTokenCount"), u.get("candidatesTokenCount")
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        u = j.get("usage", {})
        return msg.get("content") or "", u.get("prompt_tokens"), u.get("completion_tokens")

    def invoke(self, system, user, max_tokens=None):
        url, hdr, body, _ = self._request(system, user, max_tokens or self.max_tokens)
        hdr = {"Content-Type": "application/json", **hdr}
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(url, headers=hdr, json=body, timeout=240)
            except requests.RequestException as e:
                last = f"network error: {e}"
            else:
                if r.status_code == 200:
                    text, tin, tout = self._parse(r.json())
                    self.usage.add(tin, tout)
                    if not text.strip():
                        raise LLMError(f"{self.provider}/{self.model} returned an empty answer")
                    return text.strip()
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code not in RETRY:
                    raise LLMError(last)
                wait = r.headers.get("retry-after")
                if wait and wait.isdigit():
                    time.sleep(min(int(wait), 30))
                    continue
            time.sleep(min(2 ** attempt * 2, 20))
        raise LLMError(f"{self.provider}/{self.model} failed after retries: {last}")
