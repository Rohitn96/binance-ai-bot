import logging
from collections import Counter
from datetime import datetime, timezone

import requests

import config
from indicators import rank_top20

_LOG = logging.getLogger("telegram")
_SEP = "━" * 17


def send_message(text: str) -> bool:
    """Send a Telegram message. Silent-fails with error log if unreachable."""
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        _LOG.warning("Telegram not configured — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if not resp.ok:
            _LOG.error("Telegram API error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:
        _LOG.error("Telegram unreachable: %s", exc)
        return False


def _send_chunked(text: str) -> None:
    """Split messages that exceed Telegram's 4096-char limit on newline boundaries."""
    if len(text) <= 4000:
        send_message(text)
        return
    chunks = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > 4000 and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    for chunk in chunks:
        send_message(chunk)


def send_trade_alert(trade: dict, portfolio_value: float, pnl: float) -> None:
    """Fire immediately when a BUY or SELL executes. Never called for HOLD."""
    action = trade.get("action", "").upper()
    coin = trade.get("coin", "?")
    pct = trade.get("percentage", 0)
    confidence = trade.get("confidence", "?")
    reason = trade.get("reason", "")

    # fill_price from a real order, price from a dry-run
    raw_price = trade.get("fill_price") or trade.get("price") or 0
    price_str = f"${raw_price:,.4f}" if raw_price else "N/A"

    action_tag = "BUY 🟢" if action == "BUY" else "SELL 🔴"
    pnl_str = f"{pnl:+.2f}%"

    msg = (
        f"🔔 TRADE EXECUTED\n"
        f"{_SEP}\n"
        f"{action_tag} {coin}\n"
        f"Amount: {pct}% of portfolio\n"
        f"Price: {price_str}\n"
        f"Confidence: {confidence}\n"
        f"Reason: {reason}\n"
        f"\n"
        f"💼 Portfolio: ${portfolio_value:,.2f}\n"
        f"📊 P&L: {pnl_str}\n"
        f"{_SEP}"
    )
    send_message(msg)


def send_4hr_update(data: dict, indicators: dict, decisions_history: list) -> None:
    """Send compact 4-hour summary to Telegram."""
    now = datetime.now(timezone.utc)

    market = data.get("market", {})
    fng = data.get("fear_and_greed", {})
    portfolio = data.get("portfolio", {})

    fg_score = fng.get("current_value", "N/A")
    fg_label = fng.get("current_label", "Unknown")

    btc = market.get("BTCUSDT", {})
    btc_price = btc.get("price", 0)
    btc_ch24 = btc.get("change_24h_pct", 0)
    btc_str = f"${btc_price:,.2f}" if btc_price else "N/A"

    # Top opportunity from ranked coins
    top_opp = "N/A"
    try:
        cg_top20 = data.get("coingecko_top20", [])
        trending = data.get("trending_coins", [])
        candles_map = {sym: d.get("candles", []) for sym, d in market.items()}
        ranked = rank_top20(cg_top20, trending, candles_map)
        if ranked:
            top_sym = ranked[0]["symbol"].replace("USDT", "")
            top_score = ranked[0]["score"]
            top_opp = f"{top_sym} (score {top_score})"
    except Exception:
        pass

    total_usdt = portfolio.get("total_usdt", 0)
    starting = config.STARTING_CAPITAL
    pnl_pct = (total_usdt - starting) / starting * 100 if starting else 0

    trades_count = sum(1 for d in decisions_history if d.get("trade_executed", False))

    msg = (
        f"🤖 4hr Summary | {now.strftime('%H:%M UTC')}\n"
        f"{_SEP}\n"
        f"😨 F&G: {fg_score}/100 — {fg_label}\n"
        f"📈 BTC: {btc_str} ({btc_ch24:+.2f}%)\n"
        f"🏆 Top opportunity: {top_opp}\n"
        f"\n"
        f"💼 Portfolio: ${total_usdt:,.2f}\n"
        f"📊 P&L: {pnl_pct:+.2f}%\n"
        f"\n"
        f"Trades this session: {trades_count}\n"
        f"{_SEP}"
    )
    send_message(msg)


def send_daily_report(daily_stats: dict) -> None:
    """Send full daily summary message to Telegram."""
    now = datetime.now(timezone.utc)
    sep = "━" * 25

    cycles = daily_stats.get("cycles", 0)
    buys = daily_stats.get("buys", 0)
    sells = daily_stats.get("sells", 0)
    holds = daily_stats.get("holds", 0)
    trades = daily_stats.get("trades", 0)
    daily_start = daily_stats.get("daily_starting_value", config.STARTING_CAPITAL)
    current_val = daily_stats.get("current_value", daily_start)

    daily_pnl = current_val - daily_start
    daily_pct = (daily_pnl / daily_start * 100) if daily_start else 0
    total_pnl = current_val - config.STARTING_CAPITAL
    total_pct = (total_pnl / config.STARTING_CAPITAL * 100) if config.STARTING_CAPITAL else 0

    # Best / worst coin today
    start_prices = daily_stats.get("coin_start_prices", {})
    curr_prices = daily_stats.get("coin_current_prices", {})
    coin_perfs: dict[str, float] = {}
    for short in ["BTC", "ETH", "SOL", "BNB"]:
        s = start_prices.get(short, 0)
        c = curr_prices.get(short, 0)
        if s > 0 and c > 0:
            coin_perfs[short] = (c - s) / s * 100

    if coin_perfs:
        best_coin = max(coin_perfs, key=coin_perfs.get)
        worst_coin = min(coin_perfs, key=coin_perfs.get)
        best_pct = coin_perfs[best_coin]
        worst_pct = coin_perfs[worst_coin]
    else:
        best_coin = worst_coin = "N/A"
        best_pct = worst_pct = 0.0

    # Fear & Greed average
    fg_scores = daily_stats.get("fg_scores", [])
    avg_fg = int(sum(fg_scores) / len(fg_scores)) if fg_scores else 0
    if avg_fg >= 75:
        fg_avg_label = "Extreme Greed"
    elif avg_fg >= 55:
        fg_avg_label = "Greed"
    elif avg_fg >= 45:
        fg_avg_label = "Neutral"
    elif avg_fg >= 25:
        fg_avg_label = "Fear"
    else:
        fg_avg_label = "Extreme Fear"

    btc_prices = daily_stats.get("btc_prices", [0])
    btc_low = min(btc_prices) if btc_prices else 0
    btc_high = max(btc_prices) if btc_prices else 0

    decisions_list = daily_stats.get("dominant_decisions", [])
    dominant = Counter(decisions_list).most_common(1)[0][0] if decisions_list else "HOLD"
    reasons = daily_stats.get("reasons", [])
    top_reason = (Counter(reasons).most_common(1)[0][0])[:80] if reasons else "No trades"

    # Risk status
    loss_pct = (
        max(0.0, (config.STARTING_CAPITAL - current_val) / config.STARTING_CAPITAL * 100)
        if config.STARTING_CAPITAL else 0.0
    )
    stop = config.STOP_LOSS_PERCENT
    if loss_pct >= stop:
        risk_status = "🚨 CRITICAL"
    elif loss_pct >= stop * 0.5:
        risk_status = "⚠️ WARNING"
    else:
        risk_status = "✅ SAFE"

    msg = (
        f"📊 Binance Bot — Daily Report\n"
        f"{sep}\n"
        f"📅 {now.strftime('%A, %B %d %Y')}\n"
        f"\n"
        f"💰 PERFORMANCE\n"
        f"Starting capital: ${config.STARTING_CAPITAL:,.2f}\n"
        f"Current value: ${current_val:,.2f}\n"
        f"Daily change: ${daily_pnl:+,.2f} ({daily_pct:+.2f}%)\n"
        f"Total P&L: ${total_pnl:+,.2f} ({total_pct:+.2f}%)\n"
        f"\n"
        f"📈 BEST PERFORMING COIN TODAY\n"
        f"{best_coin}: {best_pct:+.2f}%\n"
        f"\n"
        f"📉 WORST PERFORMING COIN TODAY\n"
        f"{worst_coin}: {worst_pct:+.2f}%\n"
        f"\n"
        f"🧠 DECISION SUMMARY\n"
        f"Total cycles: {cycles}\n"
        f"BUY signals: {buys}\n"
        f"SELL signals: {sells}\n"
        f"HOLD signals: {holds}\n"
        f"Trades executed: {trades}\n"
        f"\n"
        f"😨 MARKET CONDITIONS TODAY\n"
        f"Fear & Greed avg: {avg_fg} ({fg_avg_label})\n"
        f"BTC range: ${btc_low:,.2f} — ${btc_high:,.2f}\n"
        f"Most common decision: {dominant}\n"
        f"Dominant reason: {top_reason}\n"
        f"\n"
        f"⚠️ RISK STATUS\n"
        f"Current loss from start: {loss_pct:.2f}%\n"
        f"Stop loss level: {stop}%\n"
        f"Status: {risk_status}\n"
        f"{sep}"
    )

    _send_chunked(msg)
