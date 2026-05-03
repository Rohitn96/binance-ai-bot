import json
import re
import anthropic

import config
from indicators import rank_top20

_STABLECOINS = {"USDC", "USDT", "TUSD", "FDUSD", "DAI", "BUSD", "USDP"}

_HOLD_RESPONSE = {
    "trades": [
        {
            "action": "HOLD",
            "coin": "NONE",
            "percentage": 0,
            "confidence": "LOW",
            "reason": "Defaulting to HOLD.",
        }
    ],
    "market_summary": "Market conditions unclear.",
    "portfolio_strategy": "Hold current positions.",
    "top_opportunity": "NONE",
}


def _fng_trend(history: list[dict]) -> str:
    if len(history) < 2:
        return "insufficient data"
    vals = [h["value"] for h in history]
    if vals[0] > vals[-1]:
        trend = "improving"
    elif vals[0] < vals[-1]:
        trend = "deteriorating"
    else:
        trend = "flat"
    return " → ".join(str(v) for v in reversed(vals)) + f" ({trend})"


def _fmt_dollar(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.2f}"


def _fmt_pct(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.{decimals}f}%"


def _build_prompt(
    global_market: dict,
    trending_coins: list[str],
    fear_greed: dict,
    ranked_coins: list[dict],
    indicators: dict,
    portfolio: dict,
    order_book: dict,
    correlation: dict,
) -> str:
    # --- GLOBAL MARKET HEALTH ---
    gm = global_market or {}
    breadth = gm.get("market_breadth", 0) or 0
    if breadth > 60:
        condition = "BULLISH"
    elif breadth < 40:
        condition = "BEARISH"
    else:
        condition = "NEUTRAL"

    _btc_dom = gm.get("btc_dominance")
    _dom_str = f"{_btc_dom:.1f}%" if _btc_dom is not None else "N/A"
    _gaining = gm.get("coins_gaining", "?")

    global_section = (
        "GLOBAL MARKET HEALTH\n"
        f"Total market cap: {_fmt_dollar(gm.get('total_market_cap_usd'))}"
        f" ({_fmt_pct(gm.get('market_cap_change_percentage_24h'))} 24h)\n"
        f"BTC dominance: {_dom_str}\n"
        f"Market volume 24h: {_fmt_dollar(gm.get('total_volume_usd'))}\n"
        f"Market breadth: {_gaining}/20 top coins rising\n"
        f"Condition: {condition}"
    )

    # --- TRENDING ---
    trending_str = ", ".join(trending_coins) if trending_coins else "none"
    trending_section = f"TRENDING RIGHT NOW\n{trending_str}"

    # --- FEAR AND GREED ---
    fng_val = fear_greed.get("current_value", "N/A")
    fng_label = fear_greed.get("current_label", "Unknown")
    fng_trend = _fng_trend(fear_greed.get("history", []))
    fng_section = (
        f"FEAR AND GREED: {fng_val}/100 — {fng_label}\n"
        f"3-day trend: {fng_trend}"
    )

    # --- TOP 20 RANKED ---
    coin_lines = []
    for rank, coin in enumerate(ranked_coins[:10], 1):
        sym = coin["symbol"]
        ind = indicators.get(sym, {})
        rsi_data = ind.get("rsi", {}) if "error" not in ind else {}
        macd_data = ind.get("macd", {}) if "error" not in ind else {}
        vol_data = ind.get("volume", {}) if "error" not in ind else {}

        rsi_val = rsi_data.get("value")
        rsi_flag = rsi_data.get("signal", "")
        rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
        if rsi_flag and rsi_flag != "NEUTRAL":
            rsi_str += f" [{rsi_flag}]"

        macd_str = macd_data.get("crossover", "N/A")

        vol_ratio = vol_data.get("ratio_vs_avg")
        vol_str = f"{vol_ratio * 100:.0f}%" if vol_ratio is not None else "N/A"

        ob = order_book.get(sym, {})
        ob_label = ob.get("pressure_label", "N/A")

        corr = correlation.get(sym)
        corr_str = f"{corr:.2f}" if corr is not None else ("1.00" if sym == "BTCUSDT" else "N/A")

        coin_lines.append(
            f"Rank {rank}: {sym} — Score {coin['score']}/100\n"
            f"  Price: ${coin['current_price']:,.4f}"
            f" | 1h: {_fmt_pct(coin['change_1h'])}"
            f" | 24h: {_fmt_pct(coin['change_24h'])}"
            f" | 7d: {_fmt_pct(coin['change_7d'])}\n"
            f"  RSI: {rsi_str} | MACD: {macd_str} | Volume vs avg: {vol_str}\n"
            f"  Order book: {ob_label}\n"
            f"  Trending: {'YES' if coin['is_trending'] else 'NO'}\n"
            f"  BTC correlation: {corr_str}"
        )

    ranked_section = "TOP 20 RANKED BY OPPORTUNITY (top 10):\n" + "\n\n".join(coin_lines)

    # --- PORTFOLIO ---
    balances = portfolio.get("balances", {})
    total_usdt = portfolio.get("total_usdt", 0)
    usdt_bal = balances.get("USDT", {})
    usdt_free = usdt_bal.get("free", 0)
    usdt_pct = usdt_bal.get("portfolio_pct", 0)

    port_lines = []
    for asset, b in balances.items():
        if asset == "USDT":
            continue
        port_lines.append(
            f"{asset}: {b.get('total', 0):.6f} units"
            f" = ${b.get('value_usdt', 0):,.2f} ({b.get('portfolio_pct', 0)}%)"
        )

    port_lines.append(f"USDT available: ${usdt_free:,.2f} ({usdt_pct}% of total)")
    portfolio_section = "CURRENT PORTFOLIO:\n" + "\n".join(port_lines)

    # --- RULES ---
    rules_section = """TRADING RULES:
- Max 5 coins held simultaneously
- Keep minimum 15% as USDT reserve
- Max 2 trades this cycle
- F&G below 15: HOLD only, no buys
- F&G 15-25: max 8% per trade
- F&G 25-40: max 15% per trade
- F&G above 40: max 20% per trade
- RSI below 28: strong buy regardless of F&G
- RSI above 72: strong sell regardless of F&G
- Trending coin with score above 60: prioritise buying
- Held coin in bottom 5 of ranking: consider selling
- Never hold 0 positions, keep at least 1 coin

IMPORTANT: HOLD is a valid and encouraged decision.
You should HOLD at least 30% of the time.
Only trade when signals are very clear and strong.
Do not force a trade every cycle."""

    return f"""{global_section}

{trending_section}

{fng_section}

{ranked_section}

{portfolio_section}

{rules_section}

Respond in this exact JSON only, no other text:
{{
  "trades": [
    {{
      "action": "BUY or SELL or HOLD",
      "coin": "symbol like BTC ETH SOL XRP",
      "percentage": number between 1 and 20,
      "confidence": "LOW or MEDIUM or HIGH",
      "reason": "one clear sentence"
    }}
  ],
  "market_summary": "one sentence",
  "portfolio_strategy": "one sentence",
  "top_opportunity": "best coin symbol right now"
}}

Maximum 2 items in trades array."""


def _parse_response(raw: str) -> dict:
    """Extract and validate the JSON trading response."""
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return {**_HOLD_RESPONSE, "trades": [{**_HOLD_RESPONSE["trades"][0], "reason": "No JSON found in AI response."}]}

    response = json.loads(json_match.group())

    required_top = {"trades", "market_summary", "portfolio_strategy", "top_opportunity"}
    if not required_top.issubset(response.keys()):
        return {**_HOLD_RESPONSE, "trades": [{**_HOLD_RESPONSE["trades"][0], "reason": "AI response missing required fields."}]}

    raw_trades = response.get("trades", [])
    if not isinstance(raw_trades, list):
        raw_trades = []

    required_trade = {"action", "coin", "percentage", "confidence", "reason"}
    normalized = []
    for trade in raw_trades[:2]:
        if not isinstance(trade, dict) or not required_trade.issubset(trade.keys()):
            continue
        trade["action"] = str(trade["action"]).upper()
        trade["coin"] = str(trade["coin"]).upper()
        trade["confidence"] = str(trade["confidence"]).upper()

        if trade["action"] not in ("BUY", "SELL", "HOLD"):
            trade["action"] = "HOLD"
        if trade["coin"] != "NONE" and not re.match(r"^[A-Z]{1,10}$", trade["coin"]):
            trade["coin"] = "NONE"
        if trade["confidence"] not in ("LOW", "MEDIUM", "HIGH"):
            trade["confidence"] = "LOW"
        if trade["confidence"] == "LOW":
            trade["percentage"] = 5

        normalized.append(trade)

    if not normalized:
        normalized = list(_HOLD_RESPONSE["trades"])

    return {
        "trades": normalized,
        "market_summary": str(response.get("market_summary", "Market conditions unclear.")),
        "portfolio_strategy": str(response.get("portfolio_strategy", "Hold current positions.")),
        "top_opportunity": str(response.get("top_opportunity", "NONE")).upper(),
    }


def _filter_aggressive_trades(
    trades: list[dict], ranked: list[dict], indicators: dict
) -> list[dict]:
    """Drop BUY trades that are stablecoins, low-score, or overbought."""
    filtered = []
    for trade in trades:
        if trade["action"] != "BUY":
            filtered.append(trade)
            continue

        coin = trade["coin"]

        # 1. Never buy stablecoins
        if coin in _STABLECOINS:
            continue

        # 2. Only buy if opportunity score > 60
        score = next(
            (rc["score"] for rc in ranked if rc["symbol"].replace("USDT", "") == coin),
            0,
        )
        if score <= 60:
            continue

        # 3. Never buy if RSI >= 68
        ind = indicators.get(coin + "USDT", indicators.get(coin, {}))
        rsi_val = ind.get("rsi", {}).get("value") if "error" not in ind else None
        if rsi_val is not None and rsi_val >= 68:
            continue

        filtered.append(trade)

    return filtered or list(_HOLD_RESPONSE["trades"])


def get_decision(data_package: dict, indicators_data: dict) -> dict:
    """Send market briefing to Claude and return a parsed multi-trade decision."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    market = data_package.get("market", {})
    candles_by_symbol = {sym: data.get("candles", []) for sym, data in market.items()}

    ranked = rank_top20(
        coingecko_top20=data_package.get("coingecko_top20", []),
        trending_coins=data_package.get("trending_coins", []),
        candles=candles_by_symbol,
    )

    prompt = _build_prompt(
        global_market=data_package.get("global_market", {}),
        trending_coins=data_package.get("trending_coins", []),
        fear_greed=data_package.get("fear_and_greed", {}),
        ranked_coins=ranked,
        indicators=indicators_data,
        portfolio=data_package.get("portfolio", {}),
        order_book=data_package.get("order_book", {}),
        correlation=data_package.get("correlation", {}),
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        result = _parse_response(raw)
        result["trades"] = _filter_aggressive_trades(result["trades"], ranked, indicators_data)
    except json.JSONDecodeError as exc:
        result = {**_HOLD_RESPONSE, "trades": [{**_HOLD_RESPONSE["trades"][0], "reason": f"JSON parse error: {exc}"}]}
    except anthropic.APIError as exc:
        result = {**_HOLD_RESPONSE, "trades": [{**_HOLD_RESPONSE["trades"][0], "reason": f"Anthropic API error: {exc}"}]}
    except Exception as exc:
        result = {**_HOLD_RESPONSE, "trades": [{**_HOLD_RESPONSE["trades"][0], "reason": f"AI engine error: {exc}"}]}

    top_opp = result.get("top_opportunity", "NONE")
    result["top_opportunity_score"] = next(
        (rc["score"] for rc in ranked if rc["symbol"].replace("USDT", "") == top_opp),
        0,
    )
    return result
