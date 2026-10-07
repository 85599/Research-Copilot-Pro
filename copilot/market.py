"""Live Indian market data.

Source order for an NSE stock: NSE website JSON -> Yahoo chart API (1m bars) -> yfinance.
BSE stocks / Sensex use Yahoo (.BO / ^BSESN). Every quote carries its source, exchange
timestamp, age and a stale flag, so a wrong or delayed price is visible instead of silent.
All of these are unofficial endpoints: fine for personal use, use a licensed feed for a public product."""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as urlquote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

IST = ZoneInfo("Asia/Kolkata")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
QUOTE_TTL = 5
_cache, _lock = {}, threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def now_ist():
    return dt.datetime.now(IST)


# ---------------------------------------------------------------- market clock
def market_status(now=None):
    """Clock-based NSE/BSE session (holidays are not tracked; a quote that stops ticking exposes them)."""
    now = now or now_ist()
    t, wd = now.time(), now.weekday()
    T = dt.time
    if wd >= 5:
        state, label = "closed", "Closed (weekend)"
    elif T(9, 0) <= t < T(9, 15):
        state, label = "preopen", "Pre-open"
    elif T(9, 15) <= t < T(15, 30):
        state, label = "open", "Market open"
    elif T(15, 30) <= t < T(16, 0):
        state, label = "closing", "Closing session"
    else:
        state, label = "closed", "Market closed"
    return {"state": state, "label": label, "now_ist": now.strftime("%d %b %Y %H:%M:%S IST"),
            "hours": "Mon-Fri 09:15-15:30 IST"}


def resolve_symbol(sym, ex="NSE"):
    """RELIANCE + NSE -> RELIANCE.NS ; keeps ^NSEI, INR=X, RELIANCE.BO as typed."""
    sym = sym.strip().upper()
    if sym.startswith("^") or "." in sym or "=" in sym:
        return sym
    return sym + (".BO" if ex == "BSE" else ".NS")


def _plain(sym):
    return not (sym.startswith("^") or "." in sym or "=" in sym)


def _num(x):
    try:
        x = float(x)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- NSE website
class _NSE:
    base = "https://www.nseindia.com"

    def __init__(self):
        self.s, self.t, self.lock = None, 0.0, threading.Lock()

    def _fresh(self):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                          "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
                          "Referer": self.base + "/", "Connection": "keep-alive"})
        s.get(self.base, timeout=8)  # sets the cookies the API insists on
        return s

    def get(self, path, params=None):
        with self.lock:
            if self.s is None or time.time() - self.t > 240:
                self.s, self.t = self._fresh(), time.time()
            s = self.s
        r = s.get(self.base + path, params=params, timeout=8)
        if r.status_code in (401, 403):
            with self.lock:
                self.s, self.t = self._fresh(), time.time()
                s = self.s
            r = s.get(self.base + path, params=params, timeout=8)
        r.raise_for_status()
        return r.json()


NSE = _NSE()


def _parse_nse_time(txt):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(txt, fmt).replace(tzinfo=IST)
        except (TypeError, ValueError):
            continue
    return None


def nse_quote(symbol):
    d = NSE.get("/api/quote-equity", {"symbol": symbol})
    pi = d["priceInfo"]
    price = _num(pi.get("lastPrice"))
    if not price:
        raise ValueError("NSE returned no lastPrice")
    hl = pi.get("intraDayHighLow") or {}
    return {"price": price, "prev_close": _num(pi.get("previousClose")), "open": _num(pi.get("open")),
            "high": _num(hl.get("max")), "low": _num(hl.get("min")), "volume": None,
            "as_of": _parse_nse_time((d.get("metadata") or {}).get("lastUpdateTime")),
            "name": (d.get("info") or {}).get("companyName"), "currency": "INR"}


def nse_indices():
    d = NSE.get("/api/allIndices")
    ts = _parse_nse_time(d.get("timestamp"))
    return {r["index"].upper(): {"price": _num(r.get("last")), "prev_close": _num(r.get("previousClose")),
                                 "high": _num(r.get("high")), "low": _num(r.get("low")),
                                 "open": _num(r.get("open")), "as_of": ts} for r in d.get("data", [])}


def nse_fii_dii():
    return NSE.get("/api/fiidiiTradeReact")


# ---------------------------------------------------------------- Yahoo
def yahoo_chart(ysym, rng="1d", interval="1m"):
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/" + urlquote(ysym, safe=""),
                     params={"range": rng, "interval": interval, "includePrePost": "false"},
                     headers={"User-Agent": UA}, timeout=8)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        raise ValueError(f"Yahoo has no data for {ysym}")
    res = res[0]
    return res["meta"], res.get("timestamp") or [], res["indicators"]["quote"][0]


def _last_valid(arr):
    for i in range(len(arr) - 1, -1, -1):
        if arr[i] is not None:
            return i
    return None


def yahoo_quote(ysym):
    meta, ts, q = yahoo_chart(ysym, "1d", "1m")
    i = _last_valid(q.get("close") or [])
    price = _num(meta.get("regularMarketPrice")) or (_num(q["close"][i]) if i is not None else None)
    if not price:
        raise ValueError("Yahoo returned no price")
    t = meta.get("regularMarketTime") or (ts[i] if i is not None else None)
    first_open = next((v for v in (q.get("open") or []) if v is not None), None)
    return {"price": price, "prev_close": _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose")),
            "open": _num(first_open), "high": _num(meta.get("regularMarketDayHigh")),
            "low": _num(meta.get("regularMarketDayLow")), "volume": _num(meta.get("regularMarketVolume")),
            "as_of": dt.datetime.fromtimestamp(t, IST) if t else None,
            "name": meta.get("longName") or meta.get("shortName"), "currency": meta.get("currency", "INR")}


def yf_quote(ysym):
    import yfinance as yf
    fi = yf.Ticker(ysym).fast_info
    price = _num(fi["last_price"])
    if not price:
        raise ValueError("yfinance returned no price")
    return {"price": price, "prev_close": _num(fi["previous_close"]), "open": _num(fi["open"]),
            "high": _num(fi["day_high"]), "low": _num(fi["day_low"]), "volume": _num(fi["last_volume"]),
            "as_of": None, "name": None, "currency": "INR"}


def _finish(raw, symbol, ex, ysym, source, errors):
    price, prev = raw["price"], raw.get("prev_close")
    chg = price - prev if prev else None
    as_of = raw.get("as_of")
    age = (now_ist() - as_of).total_seconds() if as_of else None
    ms = market_status()
    stale = bool(ms["state"] in ("open", "closing") and (age is None or age > 180))
    return {"symbol": symbol, "exchange": ex, "ysym": ysym, "name": raw.get("name") or symbol,
            "price": price, "prev_close": prev, "change": chg,
            "change_pct": (chg / prev * 100) if prev else None,
            "open": raw.get("open"), "high": raw.get("high"), "low": raw.get("low"), "volume": raw.get("volume"),
            "currency": raw.get("currency", "INR"), "source": source, "tried_errors": errors,
            "as_of": as_of.isoformat() if as_of else None,
            "as_of_label": as_of.strftime("%d %b %H:%M:%S IST") if as_of else "time unknown",
            "age_sec": age, "stale": stale, "market": ms}


def get_quote(symbol, ex="NSE", force=False):
    symbol = symbol.strip().upper()
    ysym = resolve_symbol(symbol, ex)

    def build():
        chain = []
        if ex == "NSE" and _plain(symbol):
            chain.append(("NSE.com", lambda: nse_quote(symbol)))
        chain += [("Yahoo", lambda: yahoo_quote(ysym)), ("yfinance", lambda: yf_quote(ysym))]
        errors = []
        for name, fn in chain:
            try:
                return _finish(fn(), symbol, ex, ysym, name, errors)
            except Exception as e:  # noqa: BLE001 - try the next source
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        raise LookupError(f"No live quote for {symbol} on {ex}. " + " | ".join(errors))

    return _cached(("q", ysym, ex), 0 if force else QUOTE_TTL, build)


def diag(symbol, ex="NSE"):
    """Ask every source separately so a wrong price can be traced to the source that is off."""
    symbol = symbol.strip().upper()
    ysym = resolve_symbol(symbol, ex)
    srcs = [("NSE.com", lambda: nse_quote(symbol))] if (ex == "NSE" and _plain(symbol)) else []
    srcs += [("Yahoo chart", lambda: yahoo_quote(ysym)), ("yfinance", lambda: yf_quote(ysym))]
    rows = []
    for name, fn in srcs:
        t0 = time.time()
        try:
            r = fn()
            rows.append({"source": name, "ok": True, "price": r["price"], "prev_close": r.get("prev_close"),
                         "as_of": r["as_of"].isoformat() if r.get("as_of") else None,
                         "ms": int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            rows.append({"source": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}",
                         "ms": int((time.time() - t0) * 1000)})
    prices = [r["price"] for r in rows if r["ok"]]
    spread = (max(prices) - min(prices)) / min(prices) * 100 if len(prices) > 1 else None
    return {"symbol": symbol, "exchange": ex, "ysym": ysym, "sources": rows, "spread_pct": spread,
            "market": market_status()}


# ---------------------------------------------------------------- indices / macro strip
INDEX_LIST = [("NIFTY 50", "^NSEI", "NIFTY 50"), ("SENSEX", "^BSESN", None), ("BANK NIFTY", "^NSEBANK", "NIFTY BANK"),
              ("NIFTY IT", "^CNXIT", "NIFTY IT"), ("INDIA VIX", "^INDIAVIX", "INDIA VIX")]
MACRO_LIST = [("USD/INR", "INR=X"), ("BRENT", "BZ=F")]


def get_indices(force=False):
    def build():
        nse = {}
        try:
            nse = nse_indices()
        except Exception:  # noqa: BLE001 - Yahoo covers it
            pass

        def one(item):
            name, ysym = item[0], item[1]
            nse_key = item[2] if len(item) > 2 else None
            raw, src = None, None
            if nse_key and nse.get(nse_key, {}).get("price"):
                raw, src = nse[nse_key], "NSE.com"
            else:
                try:
                    raw, src = yahoo_quote(ysym), "Yahoo"
                except Exception:  # noqa: BLE001
                    return {"name": name, "error": True}
            q = _finish(raw, name, "IDX", ysym, src, [])
            return {k: q[k] for k in ("price", "change", "change_pct", "high", "low", "source", "as_of_label", "stale")} | {"name": name, "ysym": ysym}

        items = [(n, y, k) for n, y, k in INDEX_LIST] + [(n, y, None) for n, y in MACRO_LIST]
        with ThreadPoolExecutor(6) as ex:
            rows = list(ex.map(one, items))
        return {"indices": rows[:len(INDEX_LIST)], "macro": rows[len(INDEX_LIST):], "market": market_status()}

    return _cached(("idx",), 0 if force else QUOTE_TTL, build)


# ---------------------------------------------------------------- history / chart / search
def history(ysym, start=None, end=None, period="2y"):
    """Daily OHLCV, tz-naive index. yfinance first, Yahoo chart API as fallback."""
    def build():
        df = pd.DataFrame()
        try:
            import yfinance as yf
            kw = {"start": start, "end": end} if start else {"period": period}
            df = yf.Ticker(ysym).history(auto_adjust=False, **kw)
        except Exception:  # noqa: BLE001
            df = pd.DataFrame()
        if df is None or df.empty:
            meta, ts, q = yahoo_chart(ysym, "5y" if period in ("2y", "5y") else period, "1d")
            df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"],
                               "Volume": q["volume"]}, index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST))
            if start:
                df = df[df.index >= pd.Timestamp(start, tz=IST)]
            if end:
                df = df[df.index < pd.Timestamp(end, tz=IST)]
        df = df.dropna(subset=["Close"])
        df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if pd.Timestamp(d).tzinfo else pd.Timestamp(d)
                                     for d in df.index]).normalize()
        return df[["Open", "High", "Low", "Close", "Volume"]]

    return _cached(("h", ysym, start, end, period), 60, build)


RANGES = {"1d": ("1d", "5m"), "5d": ("5d", "15m"), "1mo": ("1mo", "1d"), "6mo": ("6mo", "1d"),
          "1y": ("1y", "1d"), "5y": ("5y", "1wk")}


def chart_series(ysym, rng="1d"):
    r, iv = RANGES.get(rng, RANGES["1d"])

    def build():
        meta, ts, q = yahoo_chart(ysym, r, iv)
        rows = [(t, c, h, l, v) for t, c, h, l, v in zip(ts, q["close"], q["high"], q["low"], q["volume"]) if c is not None]
        return {"range": rng, "interval": iv, "prev_close": _num(meta.get("chartPreviousClose")),
                "t": [x[0] for x in rows], "c": [round(x[1], 2) for x in rows],
                "h": [x[2] for x in rows], "l": [x[3] for x in rows], "v": [x[4] for x in rows]}

    return _cached(("c", ysym, rng), 15 if rng in ("1d", "5d") else 300, build)


# Yahoo tags NSE indices with exchange "NSI" and BSE with "BSE"/"BSI"; normalise to what the API accepts.
_EXCH_MAP = {"NSI": "NSE", "NSE": "NSE", "BSE": "BSE", "BSI": "BSE"}
# Guaranteed Indian index hits. Yahoo's search is flaky for these ("bank nifty" returns nothing),
# so we inject them by alias. Symbols mirror INDEX_LIST and are known to quote/chart correctly.
INDEX_ALIASES = {
    "^NSEI": ("NIFTY 50", "NSE", ["nifty", "nifty50", "nifty 50", "cnxnifty", "nifty index", "nse nifty"]),
    "^NSEBANK": ("NIFTY BANK", "NSE", ["banknifty", "bank nifty", "nifty bank", "niftybank", "cnxbank"]),
    "^BSESN": ("SENSEX", "BSE", ["sensex", "bse sensex", "bsesn", "bombay sensex", "s&p bse sensex"]),
    "^CNXIT": ("NIFTY IT", "NSE", ["niftyit", "nifty it", "cnxit", "it index", "nifty information technology"]),
    "^INDIAVIX": ("INDIA VIX", "NSE", ["indiavix", "india vix", "vix", "volatility index"]),
}


def _index_hits(q):
    """Rank known Indian indices against the query so they always surface first."""
    ql = q.strip().lower()
    if len(ql) < 3:
        return []
    out = []
    for ysym, (name, ex, aliases) in INDEX_ALIASES.items():
        rank = None
        for n in [name.lower()] + [a for a in aliases if len(a) >= 3]:
            if n == ql:
                r = 0
            elif n.startswith(ql):
                r = 1
            elif ql in n:
                r = 2
            elif n in ql:
                r = 3
            else:
                continue
            rank = r if rank is None else min(rank, r)
        if rank is not None:
            out.append((rank, {"symbol": ysym, "exchange": ex, "ysym": ysym, "name": name, "type": "INDEX"}))
    out.sort(key=lambda t: t[0])
    return [o[1] for o in out]


def search(q):
    idx = _index_hits(q)
    seen = {h["symbol"] for h in idx}
    out = list(idx)
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                         params={"q": q, "quotesCount": 12, "newsCount": 0}, headers={"User-Agent": UA}, timeout=8)
        r.raise_for_status()
        raw = r.json().get("quotes", [])
    except Exception:  # noqa: BLE001 - curated indices still return
        raw = []
    for it in raw:
        s = it.get("symbol", "")
        name = it.get("longname") or it.get("shortname") or s
        if s.endswith(".NS") or s.endswith(".BO"):
            sym = s.rsplit(".", 1)[0]
            ex = "NSE" if s.endswith(".NS") else "BSE"
            row = {"symbol": sym, "exchange": ex, "ysym": s, "name": name, "type": "EQUITY"}
        elif s.startswith("^") and it.get("quoteType") == "INDEX" and (it.get("exchange") or "").upper() in _EXCH_MAP:
            ex = _EXCH_MAP[(it.get("exchange") or "").upper()]
            row = {"symbol": s, "exchange": ex, "ysym": s, "name": name, "type": "INDEX"}
        else:
            continue
        if row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        out.append(row)
    return out[:15]
