"""Dev harness: serves the real UI + API with synthetic market data and a fake LLM (no network, no keys).
   python tests/ui_harness.py   then open http://127.0.0.1:8765   -- used to smoke-test the frontend only."""
import pathlib, sys, tempfile, time, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import uvicorn
from copilot import dataflows as D, market as mk
from copilot.jobs import JobManager
from test_all import FakeLLM, fake_bundle
import server

rng = np.random.default_rng(1)
idx = pd.bdate_range(end="2026-09-30", periods=260)
close = pd.Series(2400 + np.cumsum(rng.normal(2, 20, 260)), index=idx)
H = pd.DataFrame({"Open": close, "High": close + 15, "Low": close - 15, "Close": close, "Volume": 1_000_000}, index=idx)
mk.history = lambda y, **k: H
D.identity = lambda y: {"name": "Demo Industries Ltd", "sector": "Energy", "industry": "x", "info": {"trailingPE": 22, "returnOnEquity": .14, "debtToEquity": 40, "revenueGrowth": .08, "earningsGrowth": .12}}
mk.search = lambda q: [{"symbol": q.upper(), "exchange": "NSE", "ysym": q.upper() + ".NS", "name": "Demo " + q}]
def q(sym, ex, force=False):
    if sym == "BAD": raise LookupError("No live quote for BAD on NSE. NSE.com: 403 | Yahoo: 404")
    p = 2500 + (time.time() % 10)
    return {"symbol": sym, "exchange": ex, "ysym": sym + ".NS", "name": sym.title() + " Ltd", "price": p, "prev_close": 2480.0, "change": p - 2480, "change_pct": (p - 2480) / 24.8,
            "open": 2490, "high": 2520, "low": 2470, "volume": 1, "currency": "INR", "source": "NSE.com", "tried_errors": [], "as_of": None,
            "as_of_label": "01 Oct 10:15:00 IST", "age_sec": 3, "stale": sym == "TATASTEEL", "market": mk.market_status()}
mk.get_quote = q
def idxf(force=False):
    row = lambda n, p: {"name": n, "price": p, "change": 10, "change_pct": .4, "source": "NSE.com", "as_of_label": "x", "stale": False}
    return {"indices": [row("NIFTY 50", 25000.5), row("SENSEX", 81000.2), row("BANK NIFTY", 54000), row("NIFTY IT", 40000), row("INDIA VIX", 12.3)],
            "macro": [row("USD/INR", 88.1), row("BRENT", 70.2)], "market": mk.market_status(dt.datetime(2026, 10, 1, 11, 0, tzinfo=mk.IST))}
mk.get_indices = idxf
def chart(y, rng="1d"):
    n = 80; t0 = 1759300000
    c = (2480 + np.cumsum(rng_.normal(0.5, 4, n))).round(2).tolist() if (rng_ := np.random.default_rng(2)) else []
    return {"range": rng, "interval": "5m" if rng == "1d" else "1d", "prev_close": 2480.0, "t": [t0 + i * 300 for i in range(n)], "c": c, "h": c, "l": c, "v": [1] * n}
mk.chart_series = chart
class SlowLLM(FakeLLM):
    def invoke(self, system, user, max_tokens=None):
        time.sleep(0.15); return super().invoke(system, user, max_tokens)
server.jobs = JobManager(tempfile.mkdtemp(), tempfile.mkdtemp(), make_llm=SlowLLM)
D.collect = fake_bundle
import os; os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import copilot.graph as G; G.D.collect = fake_bundle
if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="warning")
