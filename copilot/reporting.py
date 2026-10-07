"""Writes the same report tree as TradingAgents: 1_analysts/ 2_research/ 3_trading/ 4_risk/ 5_portfolio/ + complete_report.md"""
import datetime as dt
import pathlib


def _header(ticker, st, s):
    L = [f"# Trading Analysis Report: {ticker}", "", f"- Analysis date: {st['trade_date']}",
         f"- Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
         f"- Research Copilot Pro: {s['llm_provider']}, deep {s['deep_think_llm']}, quick {s['quick_think_llm']}",
         f"- Analysts: {', '.join(s['analysts'])}; research debate rounds {s['max_debate_rounds']}, risk debate rounds {s['max_risk_discuss_rounds']}"]
    if s.get("data_sources"):
        L.append("- Data: " + "; ".join(s["data_sources"]))
    return "\n".join(L) + "\n\n"


def write_report_tree(st, ticker, save_path, settings):
    root = pathlib.Path(save_path)
    root.mkdir(parents=True, exist_ok=True)
    sections = []

    def put(sub, name, text):
        d = root / sub
        d.mkdir(exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    parts = []
    for key, title, fn in (("market_report", "Market Analyst", "market.md"), ("sentiment_report", "Sentiment Analyst", "sentiment.md"),
                           ("news_report", "News Analyst", "news.md"), ("fundamentals_report", "Fundamentals Analyst", "fundamentals.md")):
        if st.get(key):
            put("1_analysts", fn, st[key])
            parts.append((title, st[key]))
    if parts:
        sections.append("## I. Analyst Team Reports\n\n" + "\n\n".join(f"### {n}\n{t}" for n, t in parts))
    deb, parts = st.get("investment_debate_state") or {}, []
    for key, title, fn in (("bull_history", "Bull Researcher", "bull.md"), ("bear_history", "Bear Researcher", "bear.md")):
        if deb.get(key):
            put("2_research", fn, deb[key])
            parts.append((title, deb[key]))
    if st.get("investment_plan"):
        put("2_research", "manager.md", st["investment_plan"])
        parts.append(("Research Manager", st["investment_plan"]))
    if parts:
        sections.append("## II. Research Team Decision\n\n" + "\n\n".join(f"### {n}\n{t}" for n, t in parts))
    if st.get("trader_investment_plan"):
        put("3_trading", "trader.md", st["trader_investment_plan"])
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{st['trader_investment_plan']}")
    rk, parts = st.get("risk_debate_state") or {}, []
    for key, title, fn in (("aggressive_history", "Aggressive Analyst", "aggressive.md"), ("conservative_history", "Conservative Analyst", "conservative.md"),
                           ("neutral_history", "Neutral Analyst", "neutral.md")):
        if rk.get(key):
            put("4_risk", fn, rk[key])
            parts.append((title, rk[key]))
    if parts:
        sections.append("## IV. Risk Management Team Decision\n\n" + "\n\n".join(f"### {n}\n{t}" for n, t in parts))
    if st.get("final_trade_decision"):
        put("5_portfolio", "decision.md", st["final_trade_decision"])
        sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{st['final_trade_decision']}")
    out = root / "complete_report.md"
    out.write_text(_header(ticker, st, settings) + "\n\n".join(sections), encoding="utf-8")
    return out
