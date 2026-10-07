"""Data 'tools' for the analysts. Python fetches and computes everything, so every number the LLM
sees is verified data (TradingAgents' 'verified snapshot' idea); the LLM only interprets it."""
import datetime as dt
import email.utils
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as urlquote

import pandas as pd
import requests

from . import indicators as ind
from . import market as mk

BENCH = {".NS": ("^NSEI", "Nifty 50"), ".BO": ("^BSESN", "Sensex")}
GLOBAL_QUERIES = ["RBI repo rate inflation India", "Nifty Sensex FII DII flows", "India GDP budget policy",
                  "crude oil rupee dollar India", "US Federal Reserve rates global markets"]
POS = "surge jump rally gain beat record upgrade buy strong profit growth bullish soar outperform dividend approval order win expansion".split()
NEG = "fall drop slump plunge loss miss downgrade sell weak probe penalty fraud bearish cut decline default resign raid fine tumble crash warning".split()


def _today():
    return mk.now_ist().date()


def _bench(ysym):
    return BENCH.get(ysym[ysym.rfind("."):], ("^NSEI", "Nifty 50"))


def fmt(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and x != x) else f"{x:,.{d}f}"


def crore(x):
    return "n/a" if x is None or x != x else f"{x / 1e7:,.0f}"


def is_historical(trade_date):
    return dt.date.fromisoformat(trade_date) < _today()


# ------------------------------------------------------------------ identity
def identity(ysym):
    try:
        import yfinance as yf
        info = yf.Ticker(ysym).info or {}
    except Exception:  # noqa: BLE001
        info = {}
    return {"name": info.get("longName") or info.get("shortName"), "sector": info.get("sector"),
            "industry": info.get("industry"), "info": info}


def instrument_context(symbol, ex, ysym, ident, trade_date):
    bits = [f"The instrument to analyze is `{ysym}` ({ex}, India; prices in INR \u20b9)."]
    if ident.get("name"):
        bits.append(f"Company: {ident['name']}.")
    if ident.get("sector") or ident.get("industry"):
        bits.append(f"Sector/industry: {ident.get('sector') or 'n/a'} / {ident.get('industry') or 'n/a'}.")
    bits.append("Use this exact ticker and company name in every tool call, report and recommendation. "
                "Do not assume US-market facts; this is an Indian listed equity (SEBI-regulated, T+1 settlement, "
                "circuit limits, FII/DII flows matter).")
    if is_historical(trade_date):
        bits.append("Note: the analysis date is in the past. Price data is cut at that date, but company profile, "
                    "news and sentiment may reflect later information; treat them with caution.")
    return " ".join(bits)


# ------------------------------------------------------------------ market
def market_pack(symbol, ex, ysym, trade_date):
    df = mk.history(ysym, period="2y")
    cut = pd.Timestamp(trade_date)
    df = df[df.index <= cut]
    if len(df) < 30:
        raise ValueError(f"Only {len(df)} daily bars for {ysym} up to {trade_date}")
    inds = ind.compute_all(df)
    cur = ind.latest(inds)
    last = df.iloc[-1]
    c = df["Close"]
    out = [f"VERIFIED MARKET SNAPSHOT \u2013 {ysym} \u2013 analysis date {trade_date}"]
    live = None
    if not is_historical(trade_date):
        try:
            live = mk.get_quote(symbol, ex, force=True)
            out.append(f"LIVE QUOTE: \u20b9{fmt(live['price'])}  prev close \u20b9{fmt(live['prev_close'])}  "
                       f"change {fmt(live['change_pct'])}%  day range \u20b9{fmt(live['low'])}-\u20b9{fmt(live['high'])}  "
                       f"source {live['source']}  as of {live['as_of_label']}  market: {live['market']['label']}"
                       + ("  [STALE: no fresh tick]" if live["stale"] else ""))
        except Exception as e:  # noqa: BLE001
            out.append(f"LIVE QUOTE: unavailable ({str(e)[:100]}). Use last close below.")
    out.append(f"Last daily bar {df.index[-1].date()}: O {fmt(last.Open)} H {fmt(last.High)} L {fmt(last.Low)} "
               f"C {fmt(last.Close)} Vol {int(last.Volume):,}")
    hi, lo = df["High"].tail(252).max(), df["Low"].tail(252).min()
    out.append(f"52-week high \u20b9{fmt(hi)} ({(c.iloc[-1] / hi - 1) * 100:.1f}% from high), low \u20b9{fmt(lo)} "
               f"({(c.iloc[-1] / lo - 1) * 100:+.1f}% from low)")
    bsym, bname = _bench(ysym)
    rel = []
    try:
        b = mk.history(bsym, period="2y")
        b = b[b.index <= cut]["Close"]
        for lab, n in (("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126)):
            if len(c) > n and len(b) > n:
                s, bb = (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, (b.iloc[-1] / b.iloc[-1 - n] - 1) * 100
                rel.append(f"{lab}: {s:+.1f}% vs {bname} {bb:+.1f}% (alpha {s - bb:+.1f} pts)")
    except Exception:  # noqa: BLE001
        rel.append(f"benchmark {bname} unavailable")
    out.append("Relative performance: " + "; ".join(rel))
    v20 = df["Volume"].tail(20).mean()
    out.append(f"Avg volume 20d {v20:,.0f}; last session volume ratio {last.Volume / v20 if v20 else 0:.2f}x")
    out.append(f"Support/resistance: 20d low \u20b9{fmt(df['Low'].tail(20).min())} / high \u20b9{fmt(df['High'].tail(20).max())}; "
               f"50d low \u20b9{fmt(df['Low'].tail(50).min())} / high \u20b9{fmt(df['High'].tail(50).max())}")
    out.append("Latest indicators: " + ", ".join(f"{k}={fmt(v)}" for k, v in cur.items()))
    out.append("Indicator meanings: close_10_ema short momentum, close_50_sma medium trend, close_200_sma long trend, "
               "macd/macds/macdh momentum, rsi 14 (70/30), boll* Bollinger 20,2, atr 14 volatility, vwma 20 volume-weighted trend.")
    t = df.tail(15).join(inds[["close_10_ema", "close_50_sma", "rsi", "macdh", "atr"]]).round(2)
    out.append("Last 15 sessions:\n" + t.reset_index().rename(columns={"index": "Date"}).to_string(index=False))
    meta = {"price": (live or {}).get("price") or float(last.Close), "atr": cur.get("atr"),
            "last_close": float(last.Close), "last_bar": str(df.index[-1].date()), "indicators": cur,
            "quote": live}
    return "\n".join(out), meta


# ------------------------------------------------------------------ fundamentals
INC = ["Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income", "Diluted EPS"]
BAL = ["Total Assets", "Total Debt", "Stockholders Equity", "Cash And Cash Equivalents", "Current Assets", "Current Liabilities"]
CF = ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]


def _stmt(df, rows, trade_date, lag_days=45, n=4):
    if df is None or df.empty:
        return "  (not available)"
    limit = pd.Timestamp(trade_date) - pd.Timedelta(days=lag_days)
    cols = [c for c in df.columns if pd.Timestamp(c) <= limit][:n]
    if not cols:
        return "  (no period reported before the analysis date)"
    lines = ["  " + " | ".join(["Item (\u20b9 crore)"] + [str(pd.Timestamp(c).date()) for c in cols])]
    for r in rows:
        if r in df.index:
            vals = [(fmt(float(df.loc[r, c]), 2) if r == "Diluted EPS" else crore(float(df.loc[r, c])))
                    if pd.notna(df.loc[r, c]) else "n/a" for c in cols]
            lines.append("  " + " | ".join([r] + vals))
    return "\n".join(lines)


def fundamentals_pack(ysym, ident, trade_date):
    import yfinance as yf
    t = yf.Ticker(ysym)
    i = ident.get("info") or {}
    g = lambda k: i.get(k)  # noqa: E731
    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    out = [f"FUNDAMENTALS \u2013 {ysym} ({ident.get('name') or ''}) \u2013 analysis date {trade_date}",
           f"Sector/industry: {ident.get('sector') or 'n/a'} / {ident.get('industry') or 'n/a'}",
           f"Market cap \u20b9{crore(g('marketCap'))} crore | trailing P/E {fmt(g('trailingPE'))} | forward P/E {fmt(g('forwardPE'))} | "
           f"P/B {fmt(g('priceToBook'))} | PEG {fmt(g('pegRatio'))} | EV/EBITDA {fmt(g('enterpriseToEbitda'))}",
           f"ROE {pct(g('returnOnEquity'))} | ROA {pct(g('returnOnAssets'))} | gross margin {pct(g('grossMargins'))} | "
           f"operating margin {pct(g('operatingMargins'))} | net margin {pct(g('profitMargins'))}",
           f"Debt/equity {fmt(g('debtToEquity'))}% | current ratio {fmt(g('currentRatio'))} | revenue growth {pct(g('revenueGrowth'))} | "
           f"earnings growth {pct(g('earningsGrowth'))} | dividend yield {fmt(g('dividendYield'))}% | beta {fmt(g('beta'))}",
           f"Insiders/promoters hold ~{pct(g('heldPercentInsiders'))}, institutions ~{pct(g('heldPercentInstitutions'))} "
           f"(Yahoo approximation; check the exchange shareholding pattern for exact promoter holding and pledge)"]
    if is_historical(trade_date):
        out.append("WARNING: ratios above are as of today, not the analysis date (look-ahead). Statements below are date-filtered.")
    for title, fn, rows in (("Annual income statement", lambda: t.income_stmt, INC),
                            ("Quarterly income statement", lambda: t.quarterly_income_stmt, INC),
                            ("Balance sheet (annual)", lambda: t.balance_sheet, BAL),
                            ("Cash flow (annual)", lambda: t.cashflow, CF)):
        try:
            out.append(f"{title}:\n" + _stmt(fn(), rows, trade_date))
        except Exception as e:  # noqa: BLE001
            out.append(f"{title}: unavailable ({type(e).__name__})")
    return "\n".join(out)


# ------------------------------------------------------------------ news
def _rss(query, limit):
    url = f"https://news.google.com/rss/search?q={urlquote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    r = requests.get(url, headers={"User-Agent": mk.UA}, timeout=10)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        d = None
        try:
            d = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:  # noqa: BLE001
            pass
        src = it.find("source")
        items.append({"title": it.findtext("title") or "", "date": d, "link": it.findtext("link"),
                      "source": (src.text if src is not None else "Google News")})
        if len(items) >= limit:
            break
    return items


def _yf_news(ysym, limit):
    import yfinance as yf
    out = []
    for n in (yf.Ticker(ysym).news or [])[:limit]:
        c = n.get("content") or n
        d = None
        try:
            d = pd.Timestamp(c.get("pubDate")).to_pydatetime() if c.get("pubDate") else (
                dt.datetime.fromtimestamp(n["providerPublishTime"], dt.timezone.utc) if n.get("providerPublishTime") else None)
        except Exception:  # noqa: BLE001
            pass
        out.append({"title": c.get("title", ""), "date": d, "link": (c.get("canonicalUrl") or {}).get("url") or c.get("link"),
                    "source": (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"})
    return out


def _window(items, trade_date, days):
    end = dt.datetime.combine(dt.date.fromisoformat(trade_date), dt.time(23, 59), tzinfo=mk.IST)
    start = end - dt.timedelta(days=days)
    keep = []
    for it in items:
        d = it["date"]
        if d is None:
            keep.append(it)
            continue
        d = d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        if start <= d <= end:
            keep.append(it)
    return sorted(keep, key=lambda x: x["date"].timestamp() if x["date"] else 0, reverse=True)


def company_news(symbol, ysym, name, trade_date, cfg):
    items, notes = [], []
    for label, fn in (("Yahoo", lambda: _yf_news(ysym, cfg["news_article_limit"])),
                      ("Google News", lambda: _rss(f'"{name}" OR {symbol} share NSE' if name else f"{symbol} share NSE", cfg["news_article_limit"]))):
        try:
            items += fn()
        except Exception as e:  # noqa: BLE001
            notes.append(f"{label} news failed ({type(e).__name__})")
    seen, uniq = set(), []
    for it in _window(items, trade_date, cfg["global_news_lookback_days"]):
        k = re.sub(r"\W+", "", it["title"].lower())[:60]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq[:cfg["news_article_limit"]], notes


def _fmt_items(items):
    return "\n".join(f"- [{it['date'].strftime('%d %b') if it['date'] else '?'}] {it['title']} ({it['source']})" for it in items) or "(none found)"


def news_pack(symbol, ysym, ident, trade_date, cfg, company=None):
    comp, notes = company if company else company_news(symbol, ysym, ident.get("name"), trade_date, cfg)
    out = [f"NEWS \u2013 {ysym} \u2013 window: {cfg['global_news_lookback_days']} days to {trade_date}",
           "Company headlines:\n" + _fmt_items(comp)]
    glob = []
    for q in GLOBAL_QUERIES:
        try:
            glob += _window(_rss(q, 4), trade_date, cfg["global_news_lookback_days"])[:3]
        except Exception as e:  # noqa: BLE001
            notes.append(f"macro query failed: {q} ({type(e).__name__})")
    out.append("Macro / market headlines (India + global):\n" + _fmt_items(glob[:cfg["global_news_article_limit"] * 2]))
    if not is_historical(trade_date):
        try:
            d = mk.get_indices()
            rows = [f"{r['name']}: {fmt(r['price'])} ({fmt(r.get('change_pct'))}%)" for r in d["indices"] + d["macro"] if not r.get("error")]
            out.append("Market snapshot now: " + "; ".join(rows))
        except Exception as e:  # noqa: BLE001
            notes.append(f"index snapshot failed ({type(e).__name__})")
        try:
            fd = mk.nse_fii_dii()
            out.append("FII/DII cash-market flows (\u20b9 crore, latest reported): " + "; ".join(
                f"{r.get('category')}: buy {r.get('buyValue')}, sell {r.get('sellValue')}, net {r.get('netValue')} ({r.get('date')})" for r in fd))
        except Exception as e:  # noqa: BLE001
            notes.append(f"FII/DII flows unavailable ({type(e).__name__})")
    if notes:
        out.append("Data gaps: " + "; ".join(notes))
    return "\n".join(out)


# ------------------------------------------------------------------ sentiment
def reddit_posts(symbol, name, limit=20):
    q = urlquote(f'"{name}" OR {symbol}' if name else symbol)
    url = ("https://www.reddit.com/r/IndianStockMarket+IndiaInvestments+IndianStreetBets+DalalStreetTalks/search.json"
           f"?q={q}&restrict_sr=1&sort=new&t=week&limit={limit}")
    r = requests.get(url, headers={"User-Agent": "research-copilot/2.0 (personal research tool)"}, timeout=10)
    r.raise_for_status()
    return [c["data"] for c in r.json()["data"]["children"]]


def sentiment_pack(symbol, ysym, ident, trade_date, cfg, company=None):
    comp, notes = company if company else company_news(symbol, ysym, ident.get("name"), trade_date, cfg)
    pos = neg = 0
    for it in comp:
        w = set(re.findall(r"[a-z]+", it["title"].lower()))
        p, n = len(w & set(POS)), len(w & set(NEG))
        pos += p > n
        neg += n > p
    out = [f"SENTIMENT INPUTS \u2013 {ysym} \u2013 {trade_date}",
           f"Headline tally (keyword-based, crude): {pos} positive, {neg} negative, {len(comp) - pos - neg} neutral of {len(comp)} headlines"]
    if is_historical(trade_date):
        out.append("Social posts skipped: analysis date is in the past and Reddit search is not date-bounded.")
    else:
        try:
            posts = reddit_posts(symbol, ident.get("name"))
            if posts:
                out.append("Reddit posts (last week; r/IndianStockMarket, r/IndiaInvestments, r/IndianStreetBets, r/DalalStreetTalks):")
                for p in posts[:15]:
                    body = re.sub(r"\s+", " ", p.get("selftext", ""))[:200]
                    out.append(f"- ({p.get('score', 0)} pts, {p.get('num_comments', 0)} comments) {p.get('title', '')} {body}")
            else:
                out.append("Reddit: no posts found for this instrument in the last week.")
        except Exception as e:  # noqa: BLE001
            out.append(f"Reddit unavailable ({type(e).__name__}); no social-post evidence this run.")
    out.append("Company headlines:\n" + _fmt_items(comp))
    return "\n".join(out)


# ------------------------------------------------------------------ orchestration
def collect(symbol, ex, trade_date, cfg, analysts, progress=lambda *_: None):
    """Fetch only what the selected analysts need; returns texts + provenance rows."""
    ysym = mk.resolve_symbol(symbol, ex)
    ident = identity(ysym)
    prov, bundle = [], {"ysym": ysym, "identity": ident, "trade_date": trade_date, "market_meta": {}}
    bundle["context"] = instrument_context(symbol, ex, ysym, ident, trade_date)
    news_needed = "news" in analysts or "sentiment" in analysts
    company = company_news(symbol, ysym, ident.get("name"), trade_date, cfg) if news_needed else None

    jobs = {}
    if "market" in analysts:
        jobs["market"] = lambda: market_pack(symbol, ex, ysym, trade_date)
    if "fundamentals" in analysts:
        jobs["fundamentals"] = lambda: fundamentals_pack(ysym, ident, trade_date)
    if "news" in analysts:
        jobs["news"] = lambda: news_pack(symbol, ysym, ident, trade_date, cfg, company)
    if "sentiment" in analysts:
        jobs["sentiment"] = lambda: sentiment_pack(symbol, ysym, ident, trade_date, cfg, company)
    now = mk.now_ist().strftime("%d %b %H:%M IST")
    with ThreadPoolExecutor(4) as pool:
        futs = {k: pool.submit(fn) for k, fn in jobs.items()}
    for k, f in futs.items():
        try:
            res = f.result()
            if k == "market":
                bundle["market"], bundle["market_meta"] = res
                src = (bundle["market_meta"].get("quote") or {}).get("source", "Yahoo daily bars")
                prov.append({"item": "Price + indicators", "source": src, "status": "ok", "at": now})
            else:
                bundle[k] = res
                prov.append({"item": k.capitalize(), "source": {"fundamentals": "Yahoo (yfinance)", "news": "Yahoo + Google News + NSE",
                                                               "sentiment": "Reddit + headlines"}[k], "status": "ok", "at": now})
        except Exception as e:  # noqa: BLE001
            bundle[k] = None
            prov.append({"item": k.capitalize(), "source": "-", "status": f"failed: {type(e).__name__}: {str(e)[:100]}", "at": now})
    bundle["provenance"] = prov
    return bundle
