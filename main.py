import json
import os
import sys
import threading
import time
from datetime import datetime, date, timezone

import schedule
from binance.client import Client

import config
import data_fetcher
import indicators
import risk_manager
import ai_engine
import executor
import logger as bot_logger
import telegram_notifier
from api_server import app as flask_app

BOT_NAME = "Binance AI Trading Bot"
_SEP = "=" * 62

_COINS = [("BTC", "BTCUSDT"), ("ETH", "ETHUSDT"), ("SOL", "SOLUSDT"), ("BNB", "BNBUSDT")]

# In-memory state
decisions_history: list[dict] = []
daily_stats: dict = {}
_last_data_package: dict | None = None
_last_indicators: dict | None = None
_current_day: date | None = None
_uptime_start: str = ""
_cycles_run: int = 0

_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "status.json")


def _init_daily_stats(portfolio_value: float) -> dict:
    return {
        "cycles": 0,
        "buys": 0,
        "sells": 0,
        "holds": 0,
        "trades": 0,
        "daily_starting_value": portfolio_value,
        "current_value": portfolio_value,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "fg_scores": [],
        "btc_prices": [],
        "coin_start_prices": {},
        "coin_current_prices": {},
        "dominant_decisions": [],
        "reasons": [],
    }


def _maybe_reset_daily_stats(current_portfolio_value: float) -> None:
    global daily_stats, _current_day
    today = datetime.now(timezone.utc).date()
    if _current_day != today:
        daily_stats = _init_daily_stats(current_portfolio_value)
        _current_day = today


def _print_banner() -> None:
    mode_tag = "[DRY RUN]" if config.DRY_RUN else "[LIVE]"
    print(_SEP)
    print(f"  {BOT_NAME}")
    print(f"  Starting Capital : ${config.STARTING_CAPITAL:,.2f} USDT")
    print(f"  Stop Loss        : {config.STOP_LOSS_PERCENT}%")
    print(f"  Dry Run          : {config.DRY_RUN}  {mode_tag}")
    print(f"  Trading Mode     : {config.TRADING_MODE.upper()}")
    print(f"  Interval         : every {config.TRADE_INTERVAL_MINUTES} minute(s)")
    print(_SEP)


def _test_connection() -> None:
    print("Testing Binance connection ...", end=" ", flush=True)
    try:
        client = Client(
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_SECRET_KEY,
            testnet=(config.TRADING_MODE == "testnet"),
        )
        if config.TRADING_MODE == "testnet":
            client.API_URL = "https://testnet.binance.vision/api"
        client.get_server_time()
        print("OK")
    except Exception as exc:
        print(f"FAILED\n  {exc}")
        sys.exit(1)


def _log_starting_portfolio() -> float:
    """Fetch and log starting portfolio. Returns total portfolio value."""
    print("Fetching starting portfolio ...", end=" ", flush=True)
    try:
        pkg = data_fetcher.fetch_all()
        portfolio = pkg["portfolio"]
        sep = "=" * 72
        bot_logger.trade_logger.info(sep)
        bot_logger.trade_logger.info(f"BOT STARTED — {BOT_NAME}")
        bot_logger.trade_logger.info(
            f"Starting Capital: ${config.STARTING_CAPITAL:,.2f} | "
            f"Stop Loss: {config.STOP_LOSS_PERCENT}% | "
            f"Mode: {config.TRADING_MODE.upper()} | "
            f"Dry Run: {config.DRY_RUN}"
        )
        bot_logger.trade_logger.info(
            f"Portfolio Value: ${portfolio['total_usdt']:,.2f} USDT"
        )
        for asset, data in portfolio.get("balances", {}).items():
            bot_logger.trade_logger.info(
                f"  {asset}: {data['total']:.6f} units"
                f" = ${data['value_usdt']:,.2f} ({data['portfolio_pct']}%)"
            )
        bot_logger.trade_logger.info(sep)
        print(f"OK — ${portfolio['total_usdt']:,.2f} USDT total")
        return portfolio["total_usdt"]
    except Exception as exc:
        print(f"WARNING — {exc}")
        return config.STARTING_CAPITAL


def _send_4hr_update() -> None:
    global _last_data_package, _last_indicators, decisions_history
    if _last_data_package is None:
        return
    try:
        telegram_notifier.send_4hr_update(
            _last_data_package,
            _last_indicators or {},
            decisions_history,
        )
    except Exception as exc:
        bot_logger.log_error(f"4hr Telegram update failed: {exc}", exc)


def _send_daily_report() -> None:
    global daily_stats
    try:
        telegram_notifier.send_daily_report(daily_stats)
    except Exception as exc:
        bot_logger.log_error(f"Daily Telegram report failed: {exc}", exc)


def _write_status_json(
    data_package: dict,
    indicators_data: dict,
    decision: dict,
    portfolio: dict,
) -> None:
    try:
        market = data_package.get("market", {})
        fng = data_package.get("fear_and_greed", {})
        gm = data_package.get("global_market", {})
        balances = portfolio.get("balances", {})
        total_usdt = portfolio.get("total_usdt", 0)

        usdt_bal = balances.get("USDT", {})
        usdt_available = usdt_bal.get("free", 0)

        pnl_dollar = total_usdt - config.STARTING_CAPITAL
        pnl_percent = (pnl_dollar / config.STARTING_CAPITAL * 100) if config.STARTING_CAPITAL else 0.0

        holdings = sorted(
            [
                {
                    "symbol": asset,
                    "value_usd": round(b.get("value_usdt", 0), 2),
                    "percentage": b.get("portfolio_pct", 0),
                }
                for asset, b in balances.items()
                if asset != "USDT"
            ],
            key=lambda x: x["value_usd"],
            reverse=True,
        )

        def _price(sym: str):
            return market.get(sym, {}).get("price")

        def _rsi(sym: str):
            return indicators_data.get(sym, {}).get("rsi", {}).get("value")

        def _macd(sym: str):
            return indicators_data.get(sym, {}).get("macd", {}).get("crossover")

        first_trade = (decision.get("trades") or [{}])[0]

        status_data = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "bot_running": True,
            "dry_run": config.DRY_RUN,
            "trading_mode": config.TRADING_MODE,
            "cycles_run": _cycles_run,
            "uptime_start": _uptime_start,
            "last_decision": {
                "action": first_trade.get("action", "HOLD"),
                "coin": first_trade.get("coin", "NONE"),
                "confidence": first_trade.get("confidence", "LOW"),
                "reason": first_trade.get("reason", ""),
                "market_summary": decision.get("market_summary", ""),
            },
            "portfolio": {
                "total_value": round(total_usdt, 2),
                "starting_capital": config.STARTING_CAPITAL,
                "pnl_dollar": round(pnl_dollar, 2),
                "pnl_percent": round(pnl_percent, 4),
                "usdt_available": round(usdt_available, 2),
                "top_holdings": holdings[:10],
            },
            "market": {
                "fear_greed_score": fng.get("current_value"),
                "fear_greed_label": fng.get("current_label"),
                "top_opportunity": decision.get("top_opportunity"),
                "top_opportunity_score": decision.get("top_opportunity_score", 0),
                "btc_dominance": gm.get("btc_dominance"),
                "market_breadth": gm.get("market_breadth"),
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
            },
        }

        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2)

    except Exception as exc:
        bot_logger.log_error(f"Failed to write status.json: {exc}", exc)


def _run_cycle() -> None:
    global _last_data_package, _last_indicators, decisions_history, daily_stats, _cycles_run

    try:
        # 1. Fetch all data
        data_package = data_fetcher.fetch_all()
        _last_data_package = data_package

        # 2. Calculate technical indicators
        indicators_data = indicators.calculate_all(data_package["market"])
        _last_indicators = indicators_data

        # 3. Risk check — exits the process on stop-loss breach
        portfolio = data_package["portfolio"]
        market = data_package["market"]
        fng = data_package["fear_and_greed"]

        _maybe_reset_daily_stats(portfolio["total_usdt"])

        risk = risk_manager.check(portfolio, bot_logger.trade_logger)
        if not risk["safe_to_trade"]:
            bot_logger.log_error("Risk check returned unsafe — skipping cycle")
            daily_stats["cycles"] = daily_stats.get("cycles", 0) + 1
            daily_stats["holds"] = daily_stats.get("holds", 0) + 1
            return

        # 4. AI trading decision
        decision = ai_engine.get_decision(data_package, indicators_data)

        # 5. Execute (or simulate) all trades in sequence
        trade_results = executor.execute_trades(
            decision.get("trades", []),
            data_package,
            bot_logger.trade_logger,
        )
        trades_executed = sum(
            1 for r in trade_results
            if r.get("status") in ("executed", "dry_run") and r.get("action") in ("BUY", "SELL")
        )

        # Send trade alert for every BUY or SELL that actually executed
        pnl_pct = (
            (portfolio["total_usdt"] - config.STARTING_CAPITAL) / config.STARTING_CAPITAL * 100
            if config.STARTING_CAPITAL else 0.0
        )
        for trade_dec, result in zip(decision.get("trades", []), trade_results):
            if (result.get("status") in ("executed", "dry_run")
                    and result.get("action") in ("BUY", "SELL")):
                try:
                    telegram_notifier.send_trade_alert(
                        {**trade_dec, **result},
                        portfolio["total_usdt"],
                        pnl_pct,
                    )
                except Exception as exc:
                    bot_logger.log_error(f"Trade alert failed: {exc}", exc)

        # Compatibility adapter for logger (uses first trade as primary decision)
        first_trade = (decision.get("trades") or [{}])[0]
        first_result = trade_results[0] if trade_results else {"status": "skipped", "reason": "no trades"}
        log_decision = {
            "action": first_trade.get("action", "HOLD"),
            "coin": first_trade.get("coin", "NONE"),
            "percentage": first_trade.get("percentage", 0),
            "confidence": first_trade.get("confidence", "LOW"),
            "reason": first_trade.get("reason", ""),
            "market_summary": decision.get("market_summary", ""),
        }

        # 6. Log full cycle summary
        bot_logger.log_cycle(
            data_package=data_package,
            indicators=indicators_data,
            decision=log_decision,
            trade_result=first_result,
            portfolio_before=portfolio,
            portfolio_after=portfolio,
            risk_before=risk,
            risk_after=risk,
        )

        # 7. Append CSV row
        bot_logger.log_csv_row(
            data_package=data_package,
            indicators=indicators_data,
            decision=log_decision,
            trade_result=first_result,
            portfolio=portfolio,
        )

        # 8. Record each trade in history
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cycle_prices = {
            "btc_price": market.get("BTCUSDT", {}).get("price", 0),
            "eth_price": market.get("ETHUSDT", {}).get("price", 0),
            "sol_price": market.get("SOLUSDT", {}).get("price", 0),
            "bnb_price": market.get("BNBUSDT", {}).get("price", 0),
            "fear_greed_score": fng.get("current_value"),
            "portfolio_value": portfolio.get("total_usdt", 0),
        }
        for trade, result in zip(decision.get("trades", [log_decision]), trade_results or [first_result]):
            executed = (
                result.get("status") in ("executed", "dry_run")
                and result.get("action") in ("BUY", "SELL")
            )
            decisions_history.append({
                "timestamp": now_str,
                "action": trade.get("action", "HOLD"),
                "coin": trade.get("coin", "NONE"),
                "confidence": trade.get("confidence", "LOW"),
                "reason": (trade.get("reason", "") or ""),
                "trade_executed": executed,
                **cycle_prices,
            })
        if len(decisions_history) > 1000:
            decisions_history = decisions_history[-1000:]

        # 9. Update daily stats
        daily_stats["cycles"] = daily_stats.get("cycles", 0) + 1
        daily_stats["current_value"] = portfolio.get("total_usdt", 0)
        daily_stats["trades"] = daily_stats.get("trades", 0) + trades_executed

        for trade in decision.get("trades", [log_decision]):
            action = trade.get("action", "HOLD")
            if action == "BUY":
                daily_stats["buys"] = daily_stats.get("buys", 0) + 1
            elif action == "SELL":
                daily_stats["sells"] = daily_stats.get("sells", 0) + 1
            else:
                daily_stats["holds"] = daily_stats.get("holds", 0) + 1
            reason = trade.get("reason", "")
            if reason:
                daily_stats.setdefault("reasons", []).append(reason)
            daily_stats.setdefault("dominant_decisions", []).append(action)

        fg_val = fng.get("current_value")
        if fg_val is not None:
            daily_stats.setdefault("fg_scores", []).append(int(fg_val))

        btc_price = market.get("BTCUSDT", {}).get("price", 0)
        if btc_price > 0:
            daily_stats.setdefault("btc_prices", []).append(btc_price)

        for short, sym in _COINS:
            price = market.get(sym, {}).get("price", 0)
            if price > 0:
                start_p = daily_stats.setdefault("coin_start_prices", {})
                if short not in start_p:
                    start_p[short] = price
                daily_stats.setdefault("coin_current_prices", {})[short] = price

        # 10. Write status.json for the API server
        _cycles_run += 1
        _write_status_json(data_package, indicators_data, decision, portfolio)

        # Console heartbeat
        fng_val = fng.get("current_value", "N/A")
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        top_opp = decision.get("top_opportunity", "?")
        print(
            f"[{now_ts}] BTC=${btc_price:,.2f}"
            f" | F&G={fng_val}"
            f" | {first_trade.get('action','HOLD')} {first_trade.get('coin','NONE')}"
            f" [{first_trade.get('confidence','LOW')}]"
            f" | {trades_executed} trade(s) | top={top_opp}"
        )

    except Exception as exc:
        bot_logger.log_error(f"Cycle error: {exc}", exc)
        print(f"[ERROR] Cycle failed: {exc} — will retry next cycle")


def main() -> None:
    global daily_stats, _current_day, _uptime_start

    required_attrs = [
        "BINANCE_API_KEY", "ANTHROPIC_API_KEY",
        "STARTING_CAPITAL", "STOP_LOSS_PERCENT",
        "DRY_RUN", "TRADING_MODE",
    ]
    missing = [a for a in required_attrs if not hasattr(config, a)]
    if missing:
        print(f"[FATAL] Config missing: {', '.join(missing)}")
        sys.exit(1)

    _uptime_start = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    api_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0", port=8080, debug=False, use_reloader=False
        )
    )
    api_thread.daemon = True
    api_thread.start()

    _print_banner()
    _test_connection()
    initial_value = _log_starting_portfolio()

    # Initialize daily stats with actual portfolio value
    _current_day = datetime.now(timezone.utc).date()
    daily_stats = _init_daily_stats(initial_value)

    # Send startup notification
    mode = "DRY RUN" if config.DRY_RUN else "LIVE"
    telegram_notifier.send_message(
        f"🚀 Binance Bot Started\n"
        f"Mode: {mode}\n"
        f"Capital: ${config.STARTING_CAPITAL:,.2f}\n"
        f"Stop Loss: {config.STOP_LOSS_PERCENT}%\n"
        f"Bot is now monitoring markets every {config.TRADE_INTERVAL_MINUTES} minutes"
    )

    print(
        f"\nRunning first cycle immediately, then every"
        f" {config.TRADE_INTERVAL_MINUTES} minute(s).\n"
    )

    _run_cycle()

    schedule.every(config.TRADE_INTERVAL_MINUTES).minutes.do(_run_cycle)
    schedule.every(4).hours.do(_send_4hr_update)
    schedule.every().day.at("08:00").do(_send_daily_report)

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
