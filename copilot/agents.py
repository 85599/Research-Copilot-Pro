"""Agent prompts. Same cast and hand-offs as TauricResearch/TradingAgents (Apache-2.0), adapted to Indian
equities: 4 analysts -> bull/bear debate -> research manager -> trader -> aggressive/conservative/neutral
risk debate -> portfolio manager. Each function is one LLM call and returns text."""
import re

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
SCALE = """**Rating Scale** (use exactly one):
- **Buy**: Strong conviction; recommend taking or growing the position
- **Overweight**: Constructive view; gradually increase exposure
- **Hold**: Balanced view; maintain the current position
- **Underweight**: Cautious view; trim exposure or take partial profits
- **Sell**: Strong conviction on the downside; exit or avoid the position"""
DECIDE = ("The debate always contains conflicting arguments; deciding which side is stronger is the job, so conflict alone is not "
          "a reason to Hold. Commit to the side with the stronger case, sized by how decisively it wins. Choose Hold only when "
          "the evidence is still balanced after weighing, or too thin to support a call. Weigh arguments on their merits, "
          "independent of who spoke first or last. Never invent data that is not in the reports.")
TABLE = " Append a Markdown table at the end of the report that organizes the key points."


def lang(cfg):
    l = (cfg.get("output_language") or "English").strip()
    if l.lower() == "english":
        return ""
    return (f" Write your entire response in {l}, except labelled lines the format asks for (e.g. \"**Rating**:\", "
            f"\"**Action**:\", \"FINAL TRANSACTION PROPOSAL:\"), which keep their English label and value exactly.")


def absent(text, label):
    return (text or "").strip() or f"(No {label} report available: that analyst was not selected or failed.)"


def _cap(text, limit):
    """Head-truncate at a line boundary so a big report still fits a small per-request token budget."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit * 0.6:
        cut = cut[:nl]
    return cut.rstrip() + "\n…[report truncated to fit the model's request-size limit]"


def _tail(text, limit):
    """Keep the most recent turns of an accumulating debate history, at a line boundary."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    if 0 <= nl < limit * 0.4:
        cut = cut[nl + 1:]
    return "…[earlier debate turns trimmed]\n" + cut


def _budget(cfg, key, default):
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _opp(text, who):
    return (text or "").strip() or f"(The {who} has not spoken yet - open the debate with your own case.)"


# ------------------------------------------------------------------ parsing
def parse_rating(text, labels="Rating|Recommendation"):
    m = re.search(rf"\*\*(?:{labels})\*\*\s*:?\s*\**\s*(Buy|Overweight|Hold|Underweight|Sell)\b", text, re.I)
    if not m:
        m = re.search(rf"(?:{labels})\s*:\s*\**\s*(Buy|Overweight|Hold|Underweight|Sell)\b", text, re.I)
    if not m:
        m = re.search(r"\b(Overweight|Underweight|Buy|Sell|Hold)\b", text)
    return m.group(1).capitalize() if m else "Hold"


def parse_action(text):
    m = re.search(r"\*\*Action\*\*\s*:?\s*\**\s*(Buy|Hold|Sell)\b", text, re.I) or re.search(
        r"FINAL TRANSACTION PROPOSAL:\s*\**\s*(BUY|HOLD|SELL)", text, re.I)
    return m.group(1).capitalize() if m else "Hold"


def parse_price(text, label):
    """First absolute price after '**Label**:'. Percentages and ranges are rejected, never guessed."""
    m = re.search(rf"\*\*{label}\*\*\s*:?\s*([^\n]*)", text, re.I)
    if not m:
        return None
    s = m.group(1)
    if re.search(r"\d\s*(?:-|\u2013|to)\s*(?:\u20b9|rs\.?)?\s*\d", s, re.I):
        return None
    for num, pct in re.findall(r"([0-9][0-9,]*\.?[0-9]*)\s*(%?)", s):
        if not pct:
            try:
                return float(num.replace(",", ""))
            except ValueError:
                continue
    return None


# ------------------------------------------------------------------ analysts
def _analyst(llm, role, task, data, bundle, cfg):
    system = ("You are a helpful AI research analyst on an Indian equities desk, collaborating with other analysts. "
              f"Report what the data supports; another agent decides the trade. Analysis date: {bundle['trade_date']}; "
              f"treat it as 'now'. {bundle['context']}\n{task}{lang(cfg)}")
    return llm.invoke(system, f"{role} data pack (verified by code; the only facts you may cite):\n\n{data}")


def market_analyst(llm, bundle, cfg):
    task = ("You are a market (technical) analyst. From the snapshot choose up to 8 complementary indicators for this "
            "situation (moving averages, MACD family, RSI, Bollinger bands, ATR, VWMA), avoid redundancy, and explain why each "
            "fits. Treat the VERIFIED MARKET SNAPSHOT as the source of truth for every price, level and indicator value; if a LIVE "
            "QUOTE is present it is the current price, and if it is marked STALE say so. Discuss trend, momentum, volatility, "
            "volume, support/resistance, and performance relative to the benchmark index. Do not claim historical bounces or exact "
            "percentage moves unless they are in the data. Write a detailed, nuanced report with specific, actionable insights." + TABLE)
    return _analyst(llm, "Market", task, absent(bundle.get("market"), "market"), bundle, cfg)


def fundamentals_analyst(llm, bundle, cfg):
    task = ("You are a fundamentals researcher. Write a comprehensive report on the company's financials: profile, valuation "
            "(P/E, P/B, EV/EBITDA vs sector context), profitability (ROE, margins), growth, leverage and liquidity, cash flow quality, "
            "and shareholding signals. Flag red flags (high debt, falling margins, weak cash conversion, promoter pledge risk if "
            "visible) and strengths. Amounts are in \u20b9 crore. Provide specific, actionable insights with evidence." + TABLE)
    return _analyst(llm, "Fundamentals", task, absent(bundle.get("fundamentals"), "fundamentals"), bundle, cfg)


def news_analyst(llm, bundle, cfg):
    task = ("You are a news researcher. Write a comprehensive report on the recent news and macro backdrop relevant for trading "
            "this stock: company-specific events, sector news, RBI/inflation/rates, FII/DII flows, rupee and crude, global cues "
            "(US Fed, geopolitics), and what they imply for the stock. Cite only headlines in the data pack, with their dates; "
            "if a data gap is listed, say so rather than filling it." + TABLE)
    return _analyst(llm, "News", task, absent(bundle.get("news"), "news"), bundle, cfg)


def sentiment_analyst(llm, bundle, cfg):
    task = ("You are a sentiment analyst gauging short-term market mood for this stock from the posts and headlines provided. "
            "Summarise the prevailing tone, the main bull and bear narratives, how strong and how recent the evidence is, and any "
            "signs of hype or panic. Use ONLY the provided posts and headlines; if there are few or none, state that sentiment "
            "evidence is thin and do not guess." + TABLE)
    return _analyst(llm, "Sentiment", task, absent(bundle.get("sentiment"), "sentiment"), bundle, cfg)


ANALYST_FN = {"market": market_analyst, "sentiment": sentiment_analyst, "news": news_analyst, "fundamentals": fundamentals_analyst}


def _reports(r, budget=2600):
    return (f"Market research report: {_cap(absent(r.get('market'), 'market'), budget)}\n"
            f"Social media sentiment report: {_cap(absent(r.get('sentiment'), 'sentiment'), budget)}\n"
            f"Latest world affairs / news report: {_cap(absent(r.get('news'), 'news'), budget)}\n"
            f"Company fundamentals report: {_cap(absent(r.get('fundamentals'), 'fundamentals'), budget)}")


# ------------------------------------------------------------------ research team
def bull(llm, ctx, reports, history, last_bear, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    opp = _cap(_opp(last_bear, "bear analyst"), _budget(cfg, "debate_history_chars", 6000))
    p = (f"You are a Bull Analyst advocating for investing in this stock. Build a strong, evidence-based case emphasizing growth "
         f"potential, competitive advantages and positive indicators, and counter the bear's arguments with specific data. "
         f"Debate conversationally and engage the bear's points directly rather than listing data.\n\n{ctx}\n{reports}\n"
         f"Debate history: {hist}\nLast bear argument: {opp}\n"
         "Deliver a compelling bull argument, refute the bear's concerns, and cite only facts present above.")
    return "Bull Analyst: " + llm.invoke("You are the Bull Researcher on an Indian equities desk." + lang(cfg), p, max_tokens=1600)


def bear(llm, ctx, reports, history, last_bull, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    opp = _cap(_opp(last_bull, "bull analyst"), _budget(cfg, "debate_history_chars", 6000))
    p = (f"You are a Bear Analyst making the case against investing in this stock. Present a well-reasoned argument emphasizing "
         f"risks, challenges and negative indicators: valuation, leverage, weak growth or margins, regulatory/sector headwinds, "
         f"technical weakness, adverse news. Counter the bull's claims with specific data. Debate conversationally.\n\n{ctx}\n{reports}\n"
         f"Debate history: {hist}\nLast bull argument: {opp}\n"
         "Deliver a compelling bear argument, refute the bull's claims, and cite only facts present above.")
    return "Bear Analyst: " + llm.invoke("You are the Bear Researcher on an Indian equities desk." + lang(cfg), p, max_tokens=1600)


def research_manager(llm, ctx, history, cfg):
    hist = _tail(history, _budget(cfg, "debate_history_chars", 6000))
    p = (f"As the Research Manager and debate facilitator, critically evaluate this debate and deliver a clear, actionable "
         f"investment plan for the trader.\n\n{ctx}\n\n{SCALE}\n\n{DECIDE}\n\n**Debate History:**\n{hist}\n\n"
         "## Output\nWrite these sections, in this order, starting with the recommendation on its own line:\n"
         "- **Recommendation**: exactly one of Buy / Overweight / Hold / Underweight / Sell\n"
         "- **Rationale**: which arguments decided it\n"
         "- **Strategic Actions**: concrete steps for the trader, sized against a standard allocation")
    return llm.invoke("You are the Research Manager." + lang(cfg), p)


# ------------------------------------------------------------------ trader
def trader(llm, ctx, plan, market_report, live_note, cfg):
    sysm = ("You are a trading agent on an Indian equities desk. Based on the research plan, give a specific buy/sell/hold "
            "proposal. Ground price levels (entry, stop-loss, target) in the technical report's price structure: current price, "
            "support/resistance, ATR and volatility. State prices as absolute rupee levels (e.g. 2450.5), never percentages or "
            f"ranges; omit a level if you cannot state a number. Size so that the loss at the stop is about "
            f"{cfg.get('risk_per_trade_pct', 1)}% of capital, no leverage. Do not use tools." + lang(cfg))
    u = (f"Research team's investment plan:\n{_cap(plan, _budget(cfg, 'debate_history_chars', 6000))}\n\n{ctx}\n{live_note}\n\nTechnical market report:\n"
         f"{_cap(absent(market_report, 'market'), 2 * _budget(cfg, 'report_char_budget', 2600))}\n\n"
         "Make an informed trading decision.\n\n## Output\nWrite these sections in this order, starting with the action:\n"
         "- **Action**: exactly one of Buy / Hold / Sell (Overweight = Buy, Underweight = Sell; conflict alone is not a Hold)\n"
         "- **Reasoning**: two to four sentences against the plan and price structure\n"
         "- **Entry Price**, **Stop Loss**, **Target Price**, **Position Sizing**: when you can state them\n"
         "End with the line: FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**")
    return llm.invoke(sysm, u)


# ------------------------------------------------------------------ risk team
RISK_ROLES = {
    "aggressive": ("Aggressive Risk Analyst", "actively champion high-reward, high-risk opportunities, emphasizing bold strategies and "
                   "upside. Counter the conservative and neutral analysts' caution with data-driven rebuttals and show where they miss opportunities."),
    "conservative": ("Conservative Risk Analyst", "protect capital, minimize volatility and ensure steady growth. Critically examine the "
                     "high-risk elements, point to losses, drawdowns, event and liquidity risk, and counter the aggressive and neutral analysts."),
    "neutral": ("Neutral Risk Analyst", "provide a balanced perspective weighing benefits and risks, challenge both the aggressive and "
                "conservative views where they over- or under-estimate risk, and advocate a moderate, diversified, sustainable approach."),
}


def risk(llm, who, ctx, reports, trader_plan, history, last, cfg):
    title, mission = RISK_ROLES[who]
    b = _budget(cfg, "debate_history_chars", 6000)
    others = "\n".join(f"Last {k} analyst response: {_cap(_opp(last.get(k), k + ' analyst'), b)}" for k in RISK_ROLES if k != who)
    hist = _tail(history, b)
    p = (f"As the {title}, your role is to {mission} Evaluate the trader's decision below.\n\nTrader's decision:\n{_cap(trader_plan, b)}\n\n"
         f"{ctx}\n{reports}\nConversation history: {hist}\n{others}\n"
         "Respond directly to the others' points. Debate conversationally, without special formatting, and cite only facts above.")
    return f"{title.split()[0]} Analyst: " + llm.invoke(f"You are the {title} on an Indian equities desk." + lang(cfg), p, max_tokens=1600)


def portfolio_manager(llm, ctx, research_plan, trader_plan, history, lessons, cfg):
    les = f"- Lessons from prior decisions and outcomes:\n{lessons}\n" if lessons else ""
    b = _budget(cfg, "debate_history_chars", 6000)
    hist = _tail(history, b)
    p = (f"As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.\n\n{ctx}\n\n{SCALE}\n\n"
         f"**Context:**\n- Research Manager's investment plan: {_cap(research_plan, b)}\n- Trader's transaction proposal: {_cap(trader_plan, b)}\n{les}\n"
         f"**Risk Analysts Debate History:**\n{hist}\n\n---\nGround every conclusion in specific evidence from the analysts. {DECIDE}\n\n"
         "## Output\nWrite these sections, in this order, starting with the rating on its own line:\n"
         "- **Rating**: exactly one of Buy / Overweight / Hold / Underweight / Sell\n"
         "- **Executive Summary**: the call and how to act on it (entry strategy, sizing, key risk levels, time horizon)\n"
         "- **Investment Thesis**: the evidence that decided it, and what would change it")
    return llm.invoke("You are the Portfolio Manager. You approve or reject the proposal." + lang(cfg), p)


def reflect(llm, decision, outcome, cfg):
    p = (f"A past decision and its realised outcome:\n\nDecision:\n{decision}\n\nOutcome: {outcome}\n\nWrite ONE paragraph (max 90 words): "
         "what the analysis got right or wrong, and one concrete lesson for future calls on similar situations.")
    return llm.invoke("You review past trading decisions with hindsight." + lang(cfg), p, max_tokens=400)
