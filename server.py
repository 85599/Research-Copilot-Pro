"""Research Copilot Pro server.
Run:  uvicorn server:app --port 8000      then open http://localhost:8000
Live data: NSE website / Yahoo (unofficial). Agents: see copilot/graph.py (TradingAgents-style pipeline)."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from copilot import config, market as mk
from copilot import dataflows as D
from copilot.jobs import JobManager

app = FastAPI(title="Research Copilot Pro")
jobs = JobManager(config.DEFAULTS["results_dir"], config.DEFAULTS["data_dir"])
EXCH = "^(NSE|BSE)$"


def _quote_or_404(sym, ex, force=False):
    try:
        return mk.get_quote(sym, ex, force)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/health")
def health():
    return {"ok": True, "market": mk.market_status()}


@app.get("/api/config")
def get_config():
    cfg = config.build_config()
    return {"providers": config.provider_status(), "depths": config.DEPTHS, "analysts": config.ANALYSTS,
            "defaults": {k: cfg[k] for k in ("llm_provider", "deep_think_llm", "quick_think_llm", "output_language",
                                              "holding_period_days", "risk_per_trade_pct")},
            "today": mk.now_ist().date().isoformat(), "market": mk.market_status()}


@app.get("/api/quote/{sym}")
def quote(sym: str, ex: str = Query("NSE", pattern=EXCH), force: bool = False):
    return _quote_or_404(sym, ex, force)


@app.get("/api/quotes")
def quotes(items: str = Query(..., description="RELIANCE:NSE,TCS:BSE")):
    pairs = [i.split(":") for i in items.split(",") if ":" in i][:40]

    def one(p):
        try:
            q = mk.get_quote(p[0], p[1])
            return {k: q[k] for k in ("symbol", "exchange", "name", "price", "change", "change_pct", "source", "as_of_label", "stale")}
        except Exception as e:  # noqa: BLE001
            return {"symbol": p[0], "exchange": p[1], "error": str(e)[:160]}

    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(one, pairs))


@app.get("/api/indices")
def indices(force: bool = False):
    return mk.get_indices(force)


@app.get("/api/chart/{sym}")
def chart(sym: str, ex: str = Query("NSE", pattern=EXCH), range: str = "1d"):
    try:
        return mk.chart_series(mk.resolve_symbol(sym, ex), range)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"No chart data for {sym}: {e}")


@app.get("/api/search")
def search(q: str = Query(..., min_length=1)):
    try:
        return mk.search(q)
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/diag/{sym}")
def diag(sym: str, ex: str = Query("NSE", pattern=EXCH)):
    """Shows what each data source returns for this symbol; use it when a price looks wrong."""
    return mk.diag(sym, ex)


@app.get("/api/stock/{sym}")
def stock(sym: str, ex: str = Query("NSE", pattern=EXCH)):
    """Daily bars + fundamentals for the client-side quant snapshot (EMA/RSI/MACD/ATR + factor scores)."""
    sym = sym.upper().strip()
    ysym = mk.resolve_symbol(sym, ex)
    try:
        h = mk.history(ysym, period="1y")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"No data for {sym} on {ex}: {e}")
    if h.empty:
        raise HTTPException(404, f"No data for {sym} on {ex}. Check the symbol.")
    ident = D.identity(ysym)
    info, n = ident.get("info") or {}, mk._num
    q = None
    try:
        q = mk.get_quote(sym, ex)
    except Exception:  # noqa: BLE001
        pass
    return {"symbol": sym, "exchange": ex, "name": ident.get("name") or sym, "sector": ident.get("sector") or "-",
            "last_bar": str(h.index[-1].date()), "dates": [str(d.date()) for d in h.index],
            "close": [round(float(v), 2) for v in h["Close"]], "high": [round(float(v), 2) for v in h["High"]],
            "low": [round(float(v), 2) for v in h["Low"]], "volume": [int(v) for v in h["Volume"]],
            "fundamentals": {"pe": n(info.get("trailingPE")), "roe": n(info.get("returnOnEquity")), "de": n(info.get("debtToEquity")),
                             "rev_growth": n(info.get("revenueGrowth")), "earn_growth": n(info.get("earningsGrowth")),
                             "mcap": n(info.get("marketCap"))}, "quote": q}


@app.post("/api/runs")
def start_run(body: dict = Body(...)):
    try:
        job = jobs.submit(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": job.id}


@app.get("/api/runs")
def list_runs():
    return jobs.history()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, since: int = 0):
    s = jobs.get(run_id, since)
    if not s:
        raise HTTPException(404, "Run not found")
    return s


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    return {"cancelled": jobs.cancel(run_id)}


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str, download: bool = False):
    p = jobs.report_file(run_id)
    if not p:
        raise HTTPException(404, "Report not ready")
    return FileResponse(p, media_type="text/markdown; charset=utf-8", filename=f"{run_id}_complete_report.md" if download else None)


@app.get("/api/decisions")
def decisions():
    return list(reversed(jobs.log.all()))[:100]


app.mount("/", StaticFiles(directory="web", html=True), name="web")
