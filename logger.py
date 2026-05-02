import csv
import logging
import os
import traceback
from datetime import datetime, timezone

import config

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

TRADES_LOG = os.path.join(_LOG_DIR, "trades.log")
ERRORS_LOG = os.path.join(_LOG_DIR, "errors.log")
CSV_LOG = os.path.join(_LOG_DIR, "daily_data.csv")

_CSV_HEADERS = [
    "timestamp", "btc_price", "eth_price", "sol_price", "bnb_price",
    "btc_rsi", "eth_rsi", "sol_rsi", "bnb_rsi",
    "btc_macd", "eth_macd", "sol_macd", "bnb_macd",
    "fear_greed_score", "fear_greed_label",
    "top_news_headline", "decision_action", "decision_coin",
    "decision_confidence", "decision_reason",
    "portfolio_value", "pnl_percent", "trade_executed",
]

_FMT = "%(asctime)s | %(levelname)-8s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _make_logger(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


trade_logger = _make_logger("trades", TRADES_LOG)
error_logger = _make_logger("errors", ERRORS_LOG)

_SEP = "-" * 72
_COIN_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BNBUSDT": "BNB"}


def log_cycle(
    data_package: dict,
    indicators: dict,
    decision: dict,
    trade_result: dict,
    portfolio_before: dict,
    portfolio_after: dict,
    risk_before: dict,
    risk_after: dict,
) -> None:
    """Write a full trading loop summary to trades.log."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    fng = data_package.get("fear_and_greed", {})
    market = data_package.get("market", {})
    news = data_package.get("news", [])

    lines = [
        _SEP,
        f"CYCLE  {now}",
        _SEP,
        f"Fear & Greed: {fng.get('current_value', 'N/A')}/100 — {fng.get('current_label', 'N/A')}",
        "",
        "MARKET SNAPSHOT:",
    ]

    for sym, name in _COIN_MAP.items():
        md = market.get(sym, {})
        ind = indicators.get(sym, {})
        if not md:
            continue
        rsi = ind.get("rsi", {})
        macd = ind.get("macd", {})
        bb = ind.get("bollinger_bands", {})
        lines.append(
            f"  {name}: ${md.get('price', 0):,.4f}"
            f" ({md.get('change_24h_pct', 0):+.2f}%)"
            f" | RSI={rsi.get('value', 'N/A')} [{rsi.get('signal', '')}]"
            f" | MACD={macd.get('crossover', '')}"
            f" | BB={bb.get('position', '')}"
        )

    lines += ["", "TOP NEWS:"]
    for n in news[:3]:
        title = n.get("title", n.get("error", ""))
        lines.append(f"  • {title[:100]}")

    lines += [
        "",
        "AI DECISION:",
        f"  Action     : {decision.get('action')}",
        f"  Coin       : {decision.get('coin')}",
        f"  Percentage : {decision.get('percentage')}%",
        f"  Confidence : {decision.get('confidence')}",
        f"  Reason     : {decision.get('reason')}",
        f"  Market mood: {decision.get('market_summary')}",
        "",
        "TRADE RESULT:",
        f"  Status     : {trade_result.get('status')}",
    ]

    status = trade_result.get("status")
    if status == "executed":
        lines += [
            f"  Order ID   : {trade_result.get('order_id')}",
            f"  Filled     : {trade_result.get('quantity')} {trade_result.get('coin')}"
            f" @ ${trade_result.get('fill_price', 0):,.4f}",
            f"  Value      : ${trade_result.get('value_usdt', 0):,.2f} USDT",
        ]
    elif status == "dry_run":
        lines += [
            f"  Dry run    : Would {trade_result.get('action')} {trade_result.get('quantity')}"
            f" {trade_result.get('coin')} @ ${trade_result.get('price', 0):,.4f}",
            f"  Reason     : {trade_result.get('reason', '')}",
        ]
    else:
        lines.append(f"  Reason     : {trade_result.get('reason', '')}")

    val_before = portfolio_before.get("total_usdt", 0)
    val_after = portfolio_after.get("total_usdt", 0)
    pnl = val_after - config.STARTING_CAPITAL
    pnl_pct = (pnl / config.STARTING_CAPITAL * 100) if config.STARTING_CAPITAL else 0

    lines += [
        "",
        "PORTFOLIO:",
        f"  Before : ${val_before:,.2f} USDT",
        f"  After  : ${val_after:,.2f} USDT",
        f"  P&L    : ${pnl:+,.2f} ({pnl_pct:+.2f}% from ${config.STARTING_CAPITAL:,.2f})",
        "",
        "RISK CHECK:",
        f"  Loss%  : {risk_after.get('loss_percent', 0):.2f}%",
    ]

    for asset, pct in risk_after.get("overconcentrated", []):
        lines.append(f"  WARNING: {asset} is {pct:.1f}% of portfolio (>60%)")

    lines.append(_SEP)

    for line in lines:
        trade_logger.info(line)


def log_error(message: str, exc: Exception | None = None) -> None:
    """Write an error with full stack trace to errors.log (and trades.log)."""
    if exc:
        tb = traceback.format_exc()
        error_logger.error("%s\n%s", message, tb)
    else:
        error_logger.error(message)
    trade_logger.error(message)


def log_csv_row(
    data_package: dict,
    indicators: dict,
    decision: dict,
    trade_result: dict,
    portfolio: dict,
) -> None:
    """Append one CSV row to logs/daily_data.csv after every cycle."""
    market = data_package.get("market", {})
    fng = data_package.get("fear_and_greed", {})
    news = data_package.get("news", [])

    def _price(sym: str) -> float | str:
        return market.get(sym, {}).get("price", "")

    def _rsi(sym: str) -> float | str:
        return indicators.get(sym, {}).get("rsi", {}).get("value", "")

    def _macd(sym: str) -> str:
        return indicators.get(sym, {}).get("macd", {}).get("crossover", "")

    top_headline = news[0].get("title", "")[:200] if news and "title" in news[0] else ""
    pv = portfolio.get("total_usdt", 0)
    pnl_pct = (pv - config.STARTING_CAPITAL) / config.STARTING_CAPITAL * 100 if config.STARTING_CAPITAL else 0
    status = trade_result.get("status", "")
    action = trade_result.get("action", "")
    traded = "yes" if status in ("executed", "dry_run") and action in ("BUY", "SELL") else "no"

    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "btc_price": _price("BTCUSDT"),
        "eth_price": _price("ETHUSDT"),
        "sol_price": _price("SOLUSDT"),
        "bnb_price": _price("BNBUSDT"),
        "btc_rsi": _rsi("BTCUSDT"),
        "eth_rsi": _rsi("ETHUSDT"),
        "sol_rsi": _rsi("SOLUSDT"),
        "bnb_rsi": _rsi("BNBUSDT"),
        "btc_macd": _macd("BTCUSDT"),
        "eth_macd": _macd("ETHUSDT"),
        "sol_macd": _macd("SOLUSDT"),
        "bnb_macd": _macd("BNBUSDT"),
        "fear_greed_score": fng.get("current_value", ""),
        "fear_greed_label": fng.get("current_label", ""),
        "top_news_headline": top_headline,
        "decision_action": decision.get("action", ""),
        "decision_coin": decision.get("coin", ""),
        "decision_confidence": decision.get("confidence", ""),
        "decision_reason": (decision.get("reason", "") or "")[:300],
        "portfolio_value": pv,
        "pnl_percent": round(pnl_pct, 4),
        "trade_executed": traded,
    }

    file_exists = os.path.isfile(CSV_LOG)
    with open(CSV_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
