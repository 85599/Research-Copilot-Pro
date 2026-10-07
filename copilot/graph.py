"""The pipeline (what LangGraph does in TradingAgents), written as plain Python so it is easy to read and debug.
analysts (parallel) -> bull/bear debate -> research manager -> trader -> risk debate -> portfolio manager."""
import pathlib
from concurrent.futures import ThreadPoolExecutor

from . import agents as A
from . import dataflows as D
from .llm import LLM, Usage
from .memory import DecisionLog
from .reporting import write_report_tree

ANALYST_LABEL = {"market": "Market Analyst", "sentiment": "Sentiment Analyst", "news": "News Analyst", "fundamentals": "Fundamentals Analyst"}


class Cancelled(Exception):
    pass


def check_levels(action, price, entry, stop, target, risk_pct):
    """Deterministic sanity checks on the trader's numbers against the live price."""
    w, ref = [], entry or price
    if price and entry and abs(entry / price - 1) > 0.05:
        w.append(f"Entry \u20b9{entry:,.2f} is {abs(entry / price - 1) * 100:.1f}% away from the current price \u20b9{price:,.2f}")
    if action in ("Buy", "Sell") and ref and stop:
        wrong = (stop >= ref) if action == "Buy" else (stop <= ref)
        if wrong:
            w.append("Stop-loss is on the wrong side of entry for this action")
        elif abs(stop / ref - 1) > 0.15:
            w.append(f"Stop-loss is {abs(stop / ref - 1) * 100:.0f}% from entry, very wide")
    if action in ("Buy", "Sell") and ref and target:
        wrong = (target <= ref) if action == "Buy" else (target >= ref)
        if wrong:
            w.append("Target is on the wrong side of entry for this action")
    sizing = None
    if action in ("Buy", "Sell") and ref and stop and abs(ref - stop) > 0:
        risk_amt = 100000 * risk_pct / 100
        sizing = {"capital": 100000, "risk_amt": risk_amt, "qty": int(risk_amt // abs(ref - stop)),
                  "rr": (abs(target - ref) / abs(ref - stop)) if target else None}
    return w, sizing


def run_pipeline(job, req, cfg, log: DecisionLog, make_llm=LLM):
    """job: copilot.jobs.Job. req: symbol, exchange, trade_date, analysts. Returns the final state dict."""
    usage = Usage()
    deep = make_llm(cfg["llm_provider"], cfg["deep_think_llm"], cfg, usage)
    quick = make_llm(cfg["llm_provider"], cfg["quick_think_llm"], cfg, usage)
    job.usage = usage
    sel = [a for a in A.ANALYST_FN if a in req["analysts"]]
    if not sel:
        raise ValueError("Select at least one analyst")

    def guard():
        if job.cancelled:
            raise Cancelled()

    # 1. data -----------------------------------------------------------
    job.phase("Fetching live data")
    bundle = D.collect(req["symbol"], req["exchange"], req["trade_date"], cfg, sel)
    job.provenance = bundle["provenance"]
    ysym, ctx = bundle["ysym"], bundle["context"]
    job.ysym, job.name = ysym, bundle["identity"].get("name") or req["symbol"]
    guard()
    try:
        log.resolve(ysym, quick, cfg)
    except Exception:  # noqa: BLE001
        pass

    st = {"trade_date": req["trade_date"], "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "",
          "investment_debate_state": {"history": "", "bull_history": "", "bear_history": "", "current_response": "", "count": 0},
          "risk_debate_state": {"history": "", "aggressive_history": "", "conservative_history": "", "neutral_history": "", "latest": "", "count": 0}}

    # 2. analysts (parallel) -------------------------------------------
    job.phase("Analyst team")
    for a in sel:
        job.agent(a, "in_progress")

    def run_analyst(a):
        try:
            guard()
            if bundle.get(a) is None:
                raise RuntimeError("its data could not be fetched, see Data provenance")
            text = A.ANALYST_FN[a](quick, bundle, cfg)
            st[f"{a}_report"] = text
            job.say(a, ANALYST_LABEL[a], text)
            job.agent(a, "completed")
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - one failed analyst must not kill the run
            job.say(a, ANALYST_LABEL[a], f"_{ANALYST_LABEL[a]} unavailable: {e}_", kind="error")
            job.agent(a, "error")

    with ThreadPoolExecutor(len(sel)) as pool:
        list(pool.map(run_analyst, sel))
    guard()
    if not any(st[f"{a}_report"] for a in sel):
        raise RuntimeError("Every analyst failed. Check the Data provenance panel and your API key.")
    reports = A._reports({k: st[f"{k}_report"] for k in A.ANALYST_FN}, A._budget(cfg, "report_char_budget", 2600))

    # 3. bull / bear debate --------------------------------------------
    job.phase("Research debate")
    d, rounds = st["investment_debate_state"], int(cfg["max_debate_rounds"])
    while d["count"] < 2 * rounds:
        guard()
        is_bull = d["count"] % 2 == 0
        who = "bull" if is_bull else "bear"
        job.agent(who, "in_progress")
        arg = (A.bull if is_bull else A.bear)(quick, ctx, reports, d["history"], d["current_response"], cfg)
        d["history"] += "\n" + arg
        d[f"{who}_history"] += "\n" + arg
        d["current_response"], d["count"] = arg, d["count"] + 1
        job.say(who, ("Bull" if is_bull else "Bear") + f" Researcher \u00b7 round {d['count'] // 2 + d['count'] % 2}", arg)
        job.agent(who, "completed")
    guard()
    job.agent("research_manager", "in_progress")
    st["investment_plan"] = A.research_manager(deep, ctx, d["history"], cfg)
    job.say("research_manager", "Research Manager", st["investment_plan"])
    job.agent("research_manager", "completed")

    # 4. trader --------------------------------------------------------
    guard()
    job.phase("Trader")
    job.agent("trader", "in_progress")
    meta = bundle["market_meta"]
    px = meta.get("price")
    live_note = ""
    if px:
        q = meta.get("quote")
        live_note = (f"Current price \u20b9{px:,.2f}" + (f" (live from {q['source']}, {q['as_of_label']})" if q else f" (last close {meta['last_bar']})")
                     + (f"; ATR(14) \u20b9{meta['atr']:,.2f}" if meta.get("atr") else ""))
    st["trader_investment_plan"] = A.trader(deep, ctx, st["investment_plan"], st["market_report"], live_note, cfg)
    job.say("trader", "Trader", st["trader_investment_plan"])
    job.agent("trader", "completed")

    # 5. risk debate ---------------------------------------------------
    job.phase("Risk debate")
    rk, order, rr = st["risk_debate_state"], ["aggressive", "conservative", "neutral"], int(cfg["max_risk_discuss_rounds"])
    last = {}
    while rk["count"] < 3 * rr:
        guard()
        who = order[rk["count"] % 3]
        job.agent(who, "in_progress")
        arg = A.risk(quick, who, ctx, reports, st["trader_investment_plan"], rk["history"], last, cfg)
        rk["history"] += "\n" + arg
        rk[f"{who}_history"] += "\n" + arg
        last[who], rk["count"] = arg, rk["count"] + 1
        job.say(who, f"{who.capitalize()} Analyst \u00b7 round {(rk['count'] - 1) // 3 + 1}", arg)
        job.agent(who, "completed")

    # 6. portfolio manager ---------------------------------------------
    guard()
    job.phase("Portfolio manager")
    job.agent("portfolio_manager", "in_progress")
    lessons = log.lessons(ysym)
    st["final_trade_decision"] = A.portfolio_manager(deep, ctx, st["investment_plan"], st["trader_investment_plan"], rk["history"], lessons, cfg)
    st["final_rating"] = A.parse_rating(st["final_trade_decision"], "Rating")
    job.say("portfolio_manager", "Portfolio Manager", st["final_trade_decision"])
    job.agent("portfolio_manager", "completed")

    # 7. result card, report tree, decision log -------------------------
    tr = st["trader_investment_plan"]
    action = A.parse_action(tr)
    entry, stop, target = A.parse_price(tr, "Entry Price"), A.parse_price(tr, "Stop Loss"), A.parse_price(tr, "Target Price")
    warns, sizing = check_levels(action, px, entry, stop, target, float(cfg.get("risk_per_trade_pct", 1)))
    result = {"rating": st["final_rating"], "research_rating": A.parse_rating(st["investment_plan"], "Recommendation"), "action": action,
              "entry": entry, "stop": stop, "target": target, "price": px, "warnings": warns, "sizing": sizing,
              "lessons_used": bool(lessons)}
    settings = {**cfg, "analysts": sel, "data_sources": [f"{p['item']}: {p['source']} ({p['status']})" for p in bundle["provenance"]]}
    path = write_report_tree(st, ysym, pathlib.Path(job.dir), settings)
    log.add({"id": job.id, "ysym": ysym, "trade_date": req["trade_date"], "rating": st["final_rating"], "ref_price": px,
             "status": "pending", "decision": st["final_trade_decision"]})
    job.report_path = str(path)
    return result
