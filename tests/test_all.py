"""Offline tests: no network, no API keys. Run: python -m pytest tests -q"""
import datetime as dt
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from copilot import agents as A, config, graph, indicators as ind, market as mk  # noqa: E402
from copilot.jobs import JobManager  # noqa: E402
from copilot.memory import DecisionLog  # noqa: E402


# ---------------------------------------------------------------- fakes
class FakeLLM:
    calls = []

    def __init__(self, provider, model, cfg, usage=None):
        self.usage, self.model = usage, model

    def invoke(self, system, user, max_tokens=None):
        FakeLLM.calls.append(system[:40])
        if self.usage:
            self.usage.add(100, 50)
        if "Research Manager" in system:
            return "**Recommendation**: Overweight\n\n**Rationale**: bulls won on growth.\n\n**Strategic Actions**: build slowly."
        if "trading agent" in system:
            return ("**Action**: Buy\n\n**Reasoning**: trend up.\n\n**Entry Price**: \u20b9101.50\n\n**Stop Loss**: 97\n\n"
                    "**Target Price**: 110\n\n**Position Sizing**: 1% risk\n\nFINAL TRANSACTION PROPOSAL: **BUY**")
        if "Portfolio Manager" in system:
            return "**Rating**: Buy\n\n**Executive Summary**: act.\n\n**Investment Thesis**: because."
        if "review past" in system:
            return "Lesson: size smaller."
        return f"[{system[:30]}] argument with table\n| a | b |"


def fake_bundle(*a, **k):
    return {"ysym": "TEST.NS", "identity": {"name": "Test Ltd", "sector": "IT"}, "trade_date": a[2], "context": "ctx",
            "market": "market data", "market_meta": {"price": 100.0, "atr": 2.0, "last_bar": "2026-09-30", "last_close": 99.0,
                                                    "quote": {"source": "NSE.com", "as_of_label": "01 Oct 10:00:00 IST"}},
            "fundamentals": "fund data", "news": "news data", "sentiment": None,
            "provenance": [{"item": "Price", "source": "NSE.com", "status": "ok", "at": "10:00"},
                           {"item": "Sentiment", "source": "-", "status": "failed: X", "at": "10:00"}]}


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(graph.D, "collect", fake_bundle)
    FakeLLM.calls = []
    return tmp_path


# ---------------------------------------------------------------- indicators
def test_indicators_basic():
    idx = pd.date_range("2026-01-01", periods=250, freq="B")
    c = pd.Series(np.linspace(100, 200, 250), index=idx)
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000}, index=idx)
    out = ind.compute_all(df)
    assert out["rsi"].iloc[-1] > 99                      # pure uptrend
    assert abs(out["close_50_sma"].iloc[-1] - c.tail(50).mean()) < 1e-9
    assert out["close_10_ema"].iloc[-1] < c.iloc[-1]     # EMA lags a rising series
    assert abs(out["atr"].iloc[-1] - 2.0) < 0.1          # constant 2-wide bars
    assert out["boll_ub"].iloc[-1] > out["boll"].iloc[-1] > out["boll_lb"].iloc[-1]
    assert out["macdh"].iloc[-1] == pytest.approx(out["macd"].iloc[-1] - out["macds"].iloc[-1])


# ---------------------------------------------------------------- market
def _yahoo_json(price=2500.0, prev=2480.0, t=None):
    t = t or int(time.time())
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "chartPreviousClose": prev, "regularMarketTime": t,
                                           "regularMarketDayHigh": price + 5, "regularMarketDayLow": price - 9,
                                           "longName": "Reliance Industries", "currency": "INR"},
                                  "timestamp": [t - 60, t],
                                  "indicators": {"quote": [{"open": [2490, 2491], "close": [price - 1, None], "high": [1, 1], "low": [1, 1], "volume": [1, 2]}]}}]}}


class Resp:
    def __init__(self, js, code=200):
        self._js, self.status_code, self.content = js, code, b""

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


def test_symbol_resolution():
    assert mk.resolve_symbol("reliance", "NSE") == "RELIANCE.NS"
    assert mk.resolve_symbol("reliance", "BSE") == "RELIANCE.BO"
    assert mk.resolve_symbol("^NSEI", "NSE") == "^NSEI"
    assert mk.resolve_symbol("500325.BO", "NSE") == "500325.BO"


def test_market_status_clock():
    ist = mk.IST
    assert mk.market_status(dt.datetime(2026, 10, 1, 10, 0, tzinfo=ist))["state"] == "open"
    assert mk.market_status(dt.datetime(2026, 10, 1, 9, 5, tzinfo=ist))["state"] == "preopen"
    assert mk.market_status(dt.datetime(2026, 10, 1, 16, 30, tzinfo=ist))["state"] == "closed"
    assert mk.market_status(dt.datetime(2026, 10, 3, 11, 0, tzinfo=ist))["state"] == "closed"   # Saturday


def test_quote_falls_back_to_yahoo_when_nse_blocks(monkeypatch):
    mk._cache.clear()
    monkeypatch.setattr(mk.NSE, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403 blocked")))
    monkeypatch.setattr(mk.requests, "get", lambda *a, **k: Resp(_yahoo_json()))
    q = mk.get_quote("RELIANCE", "NSE", force=True)
    assert q["source"] == "Yahoo" and q["price"] == 2500.0 and q["prev_close"] == 2480.0
    assert q["change"] == pytest.approx(20.0) and q["change_pct"] == pytest.approx(20 / 2480 * 100)
    assert any("NSE.com" in e for e in q["tried_errors"])
    assert q["open"] == 2490


def test_quote_prefers_nse_and_flags_stale(monkeypatch):
    mk._cache.clear()
    old = (mk.now_ist() - dt.timedelta(hours=2)).strftime("%d-%b-%Y %H:%M:%S")
    nse = {"priceInfo": {"lastPrice": 1234.5, "previousClose": 1200, "open": 1210, "intraDayHighLow": {"max": 1240, "min": 1205}},
           "metadata": {"lastUpdateTime": old}, "info": {"companyName": "Foo Ltd"}}
    monkeypatch.setattr(mk.NSE, "get", lambda *a, **k: nse)
    monkeypatch.setattr(mk, "market_status", lambda now=None: {"state": "open", "label": "Market open", "now_ist": "", "hours": ""})
    q = mk.get_quote("FOO", "NSE", force=True)
    assert q["source"] == "NSE.com" and q["name"] == "Foo Ltd" and q["high"] == 1240
    assert q["stale"] is True and q["age_sec"] > 3600


def test_bse_never_calls_nse(monkeypatch):
    mk._cache.clear()
    monkeypatch.setattr(mk.NSE, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("NSE must not be used for BSE")))
    monkeypatch.setattr(mk.requests, "get", lambda *a, **k: Resp(_yahoo_json(310.0, 300.0)))
    q = mk.get_quote("RELIANCE", "BSE", force=True)
    assert q["ysym"] == "RELIANCE.BO" and q["price"] == 310.0 and q["source"] == "Yahoo"


def test_no_source_raises_lookup(monkeypatch):
    mk._cache.clear()
    monkeypatch.setattr(mk.requests, "get", lambda *a, **k: Resp({}, 500))
    monkeypatch.setattr(mk, "yf_quote", lambda s: (_ for _ in ()).throw(ValueError("none")))
    with pytest.raises(LookupError):
        mk.get_quote("ZZZZ", "BSE", force=True)


def test_indices_use_nse_then_yahoo(monkeypatch):
    mk._cache.clear()
    ts = mk.now_ist().strftime("%d-%b-%Y %H:%M:%S")
    monkeypatch.setattr(mk, "nse_indices", lambda: {"NIFTY 50": {"price": 25000.0, "prev_close": 24900.0, "high": 25050, "low": 24800, "open": 24950,
                                                                  "as_of": mk._parse_nse_time(ts)}})
    monkeypatch.setattr(mk, "yahoo_quote", lambda y: {"price": 80000.0, "prev_close": 79000.0, "as_of": mk.now_ist(), "name": y, "currency": "INR"})
    d = mk.get_indices(force=True)
    by = {r["name"]: r for r in d["indices"]}
    assert by["NIFTY 50"]["source"] == "NSE.com" and by["NIFTY 50"]["price"] == 25000.0
    assert by["SENSEX"]["source"] == "Yahoo" and by["SENSEX"]["change_pct"] == pytest.approx(1000 / 79000 * 100)
    assert {r["name"] for r in d["macro"]} == {"USD/INR", "BRENT"}


# ---------------------------------------------------------------- agents / parsing / checks
def test_parsers():
    assert A.parse_rating("**Rating**: Overweight\nblah Sell") == "Overweight"
    assert A.parse_rating("**Recommendation**: **Underweight**", "Recommendation") == "Underweight"
    assert A.parse_rating("no label but we say Sell here") == "Sell"
    assert A.parse_rating("nothing") == "Hold"
    assert A.parse_action("**Action**: Sell\n...") == "Sell"
    t = "**Entry Price**: \u20b92,450.50\n**Stop Loss**: 15%\n**Target Price**: 2,500 - 2,600\n**X**: 1"
    assert A.parse_price(t, "Entry Price") == 2450.5
    assert A.parse_price(t, "Stop Loss") is None          # percentage rejected
    assert A.parse_price(t, "Target Price") is None       # range rejected


def test_check_levels():
    w, s = graph.check_levels("Buy", 100, 101.5, 97, 110, 1.0)
    assert w == [] and s["qty"] == int(1000 // 4.5) and s["rr"] == pytest.approx(8.5 / 4.5)
    w, _ = graph.check_levels("Buy", 100, 101, 105, 90, 1.0)
    assert any("stop" in x.lower() for x in w) and any("target" in x.lower() for x in w)
    w, _ = graph.check_levels("Buy", 100, 130, 120, 140, 1.0)
    assert any("away from the current price" in x for x in w)


def test_config_provider_switch(monkeypatch):
    c = config.build_config({"llm_provider": "openai"})
    assert c["deep_think_llm"] == "gpt-5.5" and c["quick_think_llm"] == "gpt-5.4-mini"
    c = config.build_config({"llm_provider": "groq", "deep_think_llm": "my-model"})
    assert c["deep_think_llm"] == "my-model" and c["quick_think_llm"] == ""
    assert config.build_config()["llm_provider"] == "anthropic"
    with pytest.raises(ValueError):
        config.build_config({"llm_provider": "nope"})


# ---------------------------------------------------------------- pipeline
def _run(tmp_path, depth, analysts):
    jm = JobManager(tmp_path / "runs", tmp_path / "data", make_llm=FakeLLM)
    job = jm.submit({"symbol": "test", "exchange": "NSE", "analysts": analysts, "depth": depth, "trade_date": "2026-10-01"})
    for _ in range(100):
        if job.status in ("done", "error"):
            break
        time.sleep(0.1)
    return jm, job


def test_full_pipeline_shallow(patched):
    jm, job = _run(patched, "shallow", ["market", "news", "fundamentals", "sentiment"])
    assert job.status == "done", job.error
    r = job.result
    assert r["rating"] == "Buy" and r["research_rating"] == "Overweight" and r["action"] == "Buy"
    assert (r["entry"], r["stop"], r["target"]) == (101.5, 97.0, 110.0) and r["warnings"] == []
    assert r["sizing"]["qty"] == 222
    # sentiment data was None -> that analyst errors, the rest carry on
    assert job.agents["sentiment"] == "error" and job.agents["market"] == "completed"
    assert all(job.agents[a] == "completed" for a in ("bull", "bear", "research_manager", "trader", "aggressive", "conservative", "neutral", "portfolio_manager"))
    root = job.dir
    for f in ("1_analysts/market.md", "2_research/bull.md", "2_research/bear.md", "2_research/manager.md", "3_trading/trader.md",
              "4_risk/aggressive.md", "4_risk/conservative.md", "4_risk/neutral.md", "5_portfolio/decision.md", "complete_report.md"):
        assert (root / f).exists(), f
    rep = (root / "complete_report.md").read_text(encoding="utf-8")
    for h in ("## I. Analyst Team Reports", "## II. Research Team Decision", "## III. Trading Team Plan",
              "## IV. Risk Management Team Decision", "## V. Portfolio Manager Decision"):
        assert h in rep
    assert job.usage.calls == len(FakeLLM.calls) and job.usage.tin > 0
    assert json.loads((root / "state.json").read_text(encoding="utf-8"))["status"] == "done"
    assert [d["rating"] for d in jm.log.all()] == ["Buy"]


def test_depth_controls_debate_rounds(patched):
    jm, job = _run(patched, "medium", ["market"])
    assert job.status == "done", job.error
    texts = [m for m in job.snapshot()["messages"]]
    assert sum(m["agent"] == "bull" for m in texts) == 3 and sum(m["agent"] == "bear" for m in texts) == 3
    assert sum(m["agent"] in ("aggressive", "conservative", "neutral") for m in texts) == 9
    assert FakeLLM.calls.count("You are the Bull Researcher on an Indian equiti"[:40]) == 3


def test_all_analysts_failing_is_an_error(monkeypatch, tmp_path):
    b = fake_bundle(None, None, "2026-10-01")
    b.update(market=None, fundamentals=None, news=None)
    monkeypatch.setattr(graph.D, "collect", lambda *a, **k: b)
    jm, job = _run(tmp_path, "shallow", ["market", "news"])
    assert job.status == "error" and "Every analyst failed" in job.error


def test_missing_api_key_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from copilot.llm import LLM
    jm = JobManager(tmp_path / "r", tmp_path / "d", make_llm=LLM)
    job = jm.submit({"symbol": "x", "analysts": ["market"], "trade_date": "2026-10-01"})
    for _ in range(50):
        if job.status == "error":
            break
        time.sleep(0.1)
    assert job.status == "error" and "ANTHROPIC_API_KEY" in job.error


def test_symbol_and_id_validation(tmp_path):
    jm = JobManager(tmp_path / "r", tmp_path / "d", make_llm=FakeLLM)
    with pytest.raises(ValueError):
        jm.submit({"symbol": "../../etc", "analysts": ["market"]})
    assert jm.get("../../etc/passwd") is None and jm.report_file("..\\x") is None


def test_decision_log_resolves_and_lessons(monkeypatch, tmp_path):
    idx = pd.bdate_range("2026-09-01", periods=30)
    stock = pd.Series(np.linspace(100, 130, 30), index=idx)
    bench = pd.Series(np.linspace(1000, 1030, 30), index=idx)
    monkeypatch.setattr(mk, "history", lambda y, **k: pd.DataFrame({"Close": stock if y == "TEST.NS" else bench}))
    log = DecisionLog(tmp_path)
    log.add({"id": "a", "ysym": "TEST.NS", "trade_date": "2026-09-10", "rating": "Buy", "ref_price": float(stock["2026-09-10"]), "status": "pending", "decision": "d"})
    log.add({"id": "b", "ysym": "TEST.NS", "trade_date": "2026-10-08", "rating": "Sell", "status": "pending", "decision": "d"})   # window incomplete
    log.resolve("TEST.NS", FakeLLM("anthropic", "m", {}), {"holding_period_days": 5})
    recs = {r["id"]: r for r in log.all()}
    assert recs["a"]["status"] == "resolved" and recs["a"]["raw_return"] > 0 and recs["a"]["alpha"] > 0
    assert recs["b"]["status"] == "pending"
    assert "Lesson" in log.lessons("TEST.NS") and "Buy" in log.lessons("TEST.NS")


# ---------------------------------------------------------------- HTTP layer
def test_http_flow(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setattr(graph.D, "collect", fake_bundle)
    server.jobs = JobManager(tmp_path / "runs", tmp_path / "data", make_llm=FakeLLM)
    fake_q = {"symbol": "TCS", "exchange": "NSE", "name": "TCS", "price": 4000.0, "change": 10.0, "change_pct": 0.25, "source": "Yahoo",
              "as_of_label": "x", "stale": False}
    monkeypatch.setattr(mk, "get_quote", lambda s, e, f=False: fake_q)
    c = TestClient(server.app)
    assert c.get("/api/health").json()["ok"]
    cfg = c.get("/api/config").json()
    assert cfg["depths"] == {"shallow": 1, "medium": 3, "deep": 5} and any(p["id"] == "anthropic" for p in cfg["providers"])
    assert c.get("/api/quote/TCS?ex=NSE").json()["price"] == 4000.0
    assert c.get("/api/quote/TCS?ex=XXX").status_code == 422
    assert c.get("/api/quotes?items=TCS:NSE").json()[0]["price"] == 4000.0
    assert c.post("/api/runs", json={"symbol": ""}).status_code == 400
    rid = c.post("/api/runs", json={"symbol": "TEST", "exchange": "NSE", "analysts": ["market"], "depth": "shallow", "trade_date": "2026-10-01"}).json()["id"]
    for _ in range(100):
        s = c.get(f"/api/runs/{rid}").json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["status"] == "done" and s["result"]["rating"] == "Buy" and s["n_messages"] >= 8
    assert c.get(f"/api/runs/{rid}?since={s['n_messages']}").json()["messages"] == []
    assert "Trading Analysis Report" in c.get(f"/api/runs/{rid}/report").text
    assert c.get(f"/api/runs/{rid}/report?download=true").headers["content-disposition"].startswith("attachment")
    assert c.get("/api/runs").json()[0]["id"] == rid
    assert c.get("/api/runs/nope").status_code == 404
    assert c.get("/api/decisions").json()[0]["status"] == "pending"
