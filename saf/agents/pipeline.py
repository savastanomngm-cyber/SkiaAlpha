"""Agent Pipeline v2 — claim-graded debate, abstain rule, math sizing."""
import json, time
from concurrent.futures import ThreadPoolExecutor
from .. import store, config, data
from ..ai import llm
from ..quant import score as S
from ..exec import sizing

TECH_SYS = "You are the TECHNICAL ANALYST. Analyze momentum, trend, volatility. End with 3-5 bullet 'Key Points Summary'."
NEWS_SYS = "You are the NEWS ANALYST. Cross-reference supply-chain signals. Score news impact -1.0 to +1.0."
SENT_SYS = "You are the SENTIMENT ANALYST. Gauge crowd and GEOPOLITICAL sentiment. Score -1.0 to +1.0."
FUND_SYS = "You are the FUNDAMENTALS ANALYST. Assess profitability, growth, valuation."
GEOPOL_SYS = "You are the GEOPOLITICAL ANALYST. Score geopolitical risk premium -1.0 to +1.0."
BULL_SYS = "You are the BULLISH RESEARCHER. Build the strongest case FOR investing. Max 200 words."
BEAR_SYS = "You are the BEARISH RESEARCHER. Build the strongest case AGAINST investing. Max 200 words."
JUDGE_SYS = 'Return ONLY JSON: {"winner": "BULL" or "BEAR", "confidence": 0.0-1.0, "rationale": "..."}'
TRADER_SYS = 'Return ONLY JSON: {"action": "BUY" or "SELL" or "HOLD", "confidence": 0.0-1.0, "rationale": "..."}'

def run_analysts(ticker, tech, headlines, fund):
    news_txt = "\n".join("- " + h for h in headlines) or "No recent news."
    def _tech(): return llm.complete(TECH_SYS, f"Ticker: {ticker}\nData:\n{json.dumps(tech)}")[0]
    def _news(): return llm.complete(NEWS_SYS, f"Ticker: {ticker}\nHeadlines:\n{news_txt}")[0]
    def _sent(): return llm.complete(SENT_SYS, f"Ticker: {ticker}\nHeadlines: {json.dumps(headlines)}")[0]
    def _fund(): return llm.complete(FUND_SYS, f"Ticker: {ticker}\nFundamentals:\n{json.dumps(fund or {})}")[0]
    def _geopol(): return llm.complete(GEOPOL_SYS, f"Ticker: {ticker}\nSector: {(fund or {}).get('sector', 'N/A')}\nHeadlines:\n{news_txt}")[0]
    results = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {"technical": ex.submit(_tech), "news": ex.submit(_news), "sentiment": ex.submit(_sent), "fundamentals": ex.submit(_fund), "geopolitical": ex.submit(_geopol)}
        for name, fut in futs.items():
            try: results[name] = fut.result() or "(no output)"
            except Exception as e: results[name] = f"Error: {e}"
    return results

def run_pipeline(ticker):
    ticker = ticker.upper().strip()
    cfg = config.load()
    px = store.load_prices(ticker)
    spy = store.load_prices(cfg["settings"]["benchmark"])
    fund = store.get_fundamentals(ticker)
    
    tech = {"close": float(px["px"].iloc[-1])} if not px.empty else {}
    headlines = []
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
        for n in raw[:6]:
            content = n.get("content", n) if isinstance(n, dict) else n
            if isinstance(content, dict) and content.get("title"): headlines.append(content["title"][:200])
    except Exception: pass

    analysts = run_analysts(ticker, tech, headlines, fund)
    score = S.score_v2(ticker, px.index[-1], {ticker: px}, spy, fund=fund) if not px.empty and not spy.empty else {"total": 0, "components": {}}
    
    reports_txt = "\n".join(f"### {k.upper()} ###\n{v[:600]}" for k, v in analysts.items())
    bull = llm.complete(BULL_SYS, f"Ticker: {ticker}\nREPORTS:\n{reports_txt}\nMake the bullish case.")[0]
    bear = llm.complete(BEAR_SYS, f"Ticker: {ticker}\nREPORTS:\n{reports_txt}\nBULL says: {bull}\nRebut.")[0]
    
    verdict, _ = llm.complete_json(JUDGE_SYS, f"DEBATE:\n[BULL]\n{bull}\n[BEAR]\n{bear}", temperature=0.2)
    if not verdict or "winner" not in verdict: verdict = {"winner": "BEAR", "confidence": 0.5, "rationale": "Judge unavailable."}
    
    trader, _ = llm.complete_json(TRADER_SYS, f"Ticker: {ticker}\nREPORTS:\n{reports_txt}\nDEBATE WINNER: {verdict.get('winner')}\nMake today's decision.", temperature=0.3)
    if not trader or "action" not in trader: trader = {"action": "HOLD", "confidence": 0.0, "rationale": "Trader unavailable."}
    
    trade = None
    position_opened = False
    if trader.get("action") == "BUY" and not px.empty:
        trade = sizing.position_size(ticker)
        if trade and "shares" in trade and trade["shares"] > 0:
            position_opened = True

    store.save_memory(ticker, trader.get("action"), trade.get("pct_account", 0) if trade else 0, trader.get("rationale", "")[:200])
    
    return {"ticker": ticker, "analysts": analysts, "bull": bull, "bear": bear, "verdict": verdict, "trader": trader, "trade": trade, "score": score, "position_opened": position_opened}