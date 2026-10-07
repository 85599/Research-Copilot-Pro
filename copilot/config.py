"""Config for Research Copilot Pro. Mirrors TradingAgents' DEFAULT_CONFIG idea:
one dict, every key overridable with a COPILOT_* env var or per run from the UI."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_env(path=ROOT / ".env"):
    """Tiny .env loader (no extra dependency). Real environment variables win."""
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


load_env()

# provider id -> how to talk to it. "kind" picks the wire protocol.
PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "kind": "anthropic", "key_env": "ANTHROPIC_API_KEY",
                  "url": "https://api.anthropic.com", "deep": "claude-sonnet-5-5", "quick": "claude-haiku-4-5-20251001"},
    "openai": {"label": "OpenAI", "kind": "openai", "key_env": "OPENAI_API_KEY",
               "url": "https://api.openai.com/v1", "deep": "gpt-5.5", "quick": "gpt-5.4-mini"},
    "google": {"label": "Google (Gemini)", "kind": "google", "key_env": "GOOGLE_API_KEY",
               "url": "https://generativelanguage.googleapis.com/v1beta", "deep": "", "quick": ""},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "key_env": "DEEPSEEK_API_KEY",
                 "url": "https://api.deepseek.com", "deep": "deepseek-reasoner", "quick": "deepseek-chat"},
    "groq": {"label": "Groq", "kind": "openai", "key_env": "GROQ_API_KEY",
             "url": "https://api.groq.com/openai/v1", "deep": "", "quick": ""},
    "openrouter": {"label": "OpenRouter", "kind": "openai", "key_env": "OPENROUTER_API_KEY",
                   "url": "https://openrouter.ai/api/v1", "deep": "", "quick": ""},
    "ollama": {"label": "Ollama (local)", "kind": "openai", "key_env": None,
               "url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), "deep": "", "quick": ""},
    "openai_compatible": {"label": "OpenAI-compatible (vLLM, LM Studio...)", "kind": "openai",
                          "key_env": "OPENAI_COMPATIBLE_API_KEY", "url": "http://localhost:1234/v1", "deep": "", "quick": ""},
}

# Same three depths as the TradingAgents CLI.
DEPTHS = {"shallow": 1, "medium": 3, "deep": 5}

ANALYSTS = ["market", "sentiment", "news", "fundamentals"]

DEFAULTS = {
    "llm_provider": "anthropic",
    "deep_think_llm": "claude-sonnet-5-5",     # research manager, trader, portfolio manager
    "quick_think_llm": "claude-haiku-4-5-20251001",  # analysts, debaters
    "backend_url": None,
    "output_language": "English",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "temperature": None,
    "max_tokens": 4096,
    "llm_max_retries": 3,
    "holding_period_days": 5,        # outcome window for the decision log / reflection
    "risk_per_trade_pct": 1.0,       # sizing rule the trader must respect
    "news_article_limit": 15,
    "global_news_article_limit": 8,
    "global_news_lookback_days": 7,
    "report_char_budget": 2600,      # max chars of each analyst report embedded in debate/risk prompts
    "debate_history_chars": 6000,    # max chars of accumulated debate history kept in each prompt
    "quote_ttl": 5,                  # seconds a live quote is cached
    "results_dir": str(ROOT / "runs"),
    "data_dir": str(ROOT / "data"),
}

ENV_OVERRIDES = {
    "COPILOT_LLM_PROVIDER": "llm_provider",
    "COPILOT_DEEP_THINK_LLM": "deep_think_llm",
    "COPILOT_QUICK_THINK_LLM": "quick_think_llm",
    "COPILOT_LLM_BACKEND_URL": "backend_url",
    "COPILOT_OUTPUT_LANGUAGE": "output_language",
    "COPILOT_MAX_DEBATE_ROUNDS": "max_debate_rounds",
    "COPILOT_MAX_RISK_ROUNDS": "max_risk_discuss_rounds",
    "COPILOT_REPORT_CHAR_BUDGET": "report_char_budget",
    "COPILOT_DEBATE_HISTORY_CHARS": "debate_history_chars",
    "COPILOT_TEMPERATURE": "temperature",
    "COPILOT_MAX_TOKENS": "max_tokens",
    "COPILOT_HOLDING_PERIOD_DAYS": "holding_period_days",
    "COPILOT_RISK_PER_TRADE_PCT": "risk_per_trade_pct",
}


def build_config(overrides=None):
    """DEFAULTS < COPILOT_* env vars < per-run overrides (from the UI). Blank values are ignored."""
    cfg = dict(DEFAULTS)
    for env, key in ENV_OVERRIDES.items():
        raw = os.environ.get(env)
        if raw in (None, ""):
            continue
        ref = DEFAULTS[key]
        if key == "temperature":
            cfg[key] = float(raw)
        elif isinstance(ref, bool):
            cfg[key] = raw.lower() in ("1", "true", "yes", "on")
        elif isinstance(ref, (int, float)):
            cfg[key] = type(ref)(raw)
        else:
            cfg[key] = raw
    ov = {k: v for k, v in (overrides or {}).items() if k in cfg and v not in (None, "")}
    changed = "llm_provider" in ov and ov["llm_provider"] != cfg["llm_provider"]
    cfg.update(ov)
    if cfg["llm_provider"] not in PROVIDERS:
        raise ValueError(f"Unknown provider {cfg['llm_provider']!r}")
    if changed:  # models of the previous provider are meaningless here
        prov = PROVIDERS[cfg["llm_provider"]]
        cfg["deep_think_llm"] = ov.get("deep_think_llm") or prov["deep"]
        cfg["quick_think_llm"] = ov.get("quick_think_llm") or prov["quick"]
    return cfg


def provider_status():
    out = []
    for pid, p in PROVIDERS.items():
        has = True if p["key_env"] is None else bool(os.environ.get(p["key_env"]))
        out.append({"id": pid, "label": p["label"], "has_key": has, "key_env": p["key_env"],
                    "deep": p["deep"], "quick": p["quick"]})
    return out
