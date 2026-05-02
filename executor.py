import logging
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config

# Step-size precision for known coins; unknown coins default to 2
_QUANTITY_PRECISION = {
    "BTC": 5,
    "ETH": 4,
    "SOL": 2,
    "BNB": 3,
}

_LOG = logging.getLogger("trades")


def _build_client() -> Client:
    client = Client(
        api_key=config.BINANCE_API_KEY,
        api_secret=config.BINANCE_SECRET_KEY,
        testnet=(config.TRADING_MODE == "testnet"),
    )
    if config.TRADING_MODE == "testnet":
        client.API_URL = "https://testnet.binance.vision/api"
    return client


def _round_qty(qty: float, coin: str) -> float:
    return round(qty, _QUANTITY_PRECISION.get(coin, 2))


def _execute_single(
    trade: dict, portfolio: dict, market: dict, logger: logging.Logger
) -> dict:
    """Execute one trade decision. Returns a result dict."""
    action = trade.get("action", "HOLD").upper()
    coin = trade.get("coin", "NONE").upper()
    confidence = trade.get("confidence", "LOW").upper()
    pct = float(trade.get("percentage", 0))
    reason = trade.get("reason", "")

    if action == "HOLD" or coin == "NONE":
        logger.info(f"TRADE SKIPPED — HOLD | {reason}")
        return {"status": "skipped", "action": "HOLD", "coin": "NONE", "reason": reason}

    if confidence == "LOW":
        logger.info(f"TRADE SKIPPED — LOW confidence | {reason}")
        return {"status": "skipped", "action": "HOLD", "coin": coin,
                "reason": f"LOW confidence: {reason}"}

    symbol = f"{coin}USDT"
    total_usdt = portfolio.get("total_usdt", 0)
    balances = portfolio.get("balances", {})

    # Get price from market cache; fall back to live ticker for top-20 coins
    current_price = market.get(symbol, {}).get("price", 0)
    if current_price <= 0:
        if config.DRY_RUN:
            return {"status": "error", "reason": f"No price data for {symbol} (dry run)"}
        try:
            ticker = _build_client().get_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
        except Exception as exc:
            return {"status": "error", "reason": f"Cannot fetch price for {symbol}: {exc}"}

    trade_value_usdt = total_usdt * (pct / 100.0)
    quantity = _round_qty(trade_value_usdt / current_price, coin)

    if config.DRY_RUN:
        logger.info(
            f"DRY RUN — {action} {quantity} {coin}"
            f" @ ~${current_price:,.4f} (~${trade_value_usdt:,.2f} USDT, {pct}%)"
        )
        return {
            "status": "dry_run",
            "action": action,
            "coin": coin,
            "symbol": symbol,
            "quantity": quantity,
            "price": current_price,
            "value_usdt": round(trade_value_usdt, 2),
            "reason": reason,
        }

    client = _build_client()
    try:
        if action == "BUY":
            usdt_free = balances.get("USDT", {}).get("free", 0)
            if usdt_free < trade_value_usdt:
                msg = (f"TRADE SKIPPED — insufficient USDT:"
                       f" need ${trade_value_usdt:,.2f}, have ${usdt_free:,.2f}")
                logger.warning(msg)
                return {"status": "skipped", "reason": msg, "action": "HOLD"}
            order = client.order_market_buy(symbol=symbol, quantity=quantity)

        elif action == "SELL":
            coin_free = balances.get(coin, {}).get("free", 0)
            if coin_free < quantity:
                msg = (f"TRADE SKIPPED — insufficient {coin}:"
                       f" need {quantity}, have {coin_free}")
                logger.warning(msg)
                return {"status": "skipped", "reason": msg, "action": "HOLD"}
            order = client.order_market_sell(symbol=symbol, quantity=quantity)

        else:
            return {"status": "skipped", "reason": f"Unknown action: {action}"}

        time.sleep(1)

        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            fill_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
        else:
            fill_price = current_price
        filled_qty = float(order.get("executedQty", quantity))
        filled_value = round(fill_price * filled_qty, 2)

        logger.info(
            f"ORDER FILLED — {action} {filled_qty} {coin}"
            f" @ ${fill_price:,.4f} = ${filled_value:,.2f} USDT"
            f" | orderId={order.get('orderId')}"
        )
        return {
            "status": "executed",
            "action": action,
            "coin": coin,
            "symbol": symbol,
            "order_id": order.get("orderId"),
            "quantity": filled_qty,
            "fill_price": round(fill_price, 6),
            "value_usdt": filled_value,
            "reason": reason,
        }

    except BinanceAPIException as exc:
        msg = f"Binance API error {exc.status_code}: {exc.message}"
        logger.error(msg)
        return {"status": "error", "reason": msg}
    except Exception as exc:
        logger.error(f"Executor error: {exc}", exc_info=True)
        return {"status": "error", "reason": str(exc)}


def execute_trades(
    trades_list: list[dict], data: dict, logger: logging.Logger | None = None
) -> list[dict]:
    """
    Execute a sequence of trade decisions in order.
    Updates available USDT between trades and stops early if the
    USDT reserve falls below the minimum threshold.
    Returns a list of result dicts, one per trade attempted.
    """
    log = logger or _LOG
    portfolio = data.get("portfolio", {})
    market = data.get("market", {})

    # Mutable copies for intra-cycle balance tracking
    balances = {k: dict(v) for k, v in portfolio.get("balances", {}).items()}
    total_usdt = portfolio.get("total_usdt", 0.0)

    results: list[dict] = []
    trades_done = 0

    for trade in trades_list:
        if trades_done >= config.MAX_TRADES_PER_CYCLE:
            break

        action = trade.get("action", "HOLD").upper()

        # Reserve check before each BUY
        if action == "BUY" and total_usdt > 0:
            usdt_free = balances.get("USDT", {}).get("free", 0.0)
            reserve_pct = usdt_free / total_usdt * 100
            if reserve_pct < config.MIN_USDT_RESERVE_PCT:
                log.warning(
                    f"USDT reserve {reserve_pct:.1f}% below"
                    f" {config.MIN_USDT_RESERVE_PCT}% minimum — stopping trade sequence"
                )
                results.append({
                    "status": "stopped",
                    "action": "HOLD",
                    "reason": f"USDT reserve limit ({config.MIN_USDT_RESERVE_PCT}%) hit",
                })
                break

        current_portfolio = {"balances": balances, "total_usdt": total_usdt}
        result = _execute_single(trade, current_portfolio, market, log)
        results.append(result)

        # Update running USDT balance for subsequent trades
        if result.get("status") in ("executed", "dry_run") and action in ("BUY", "SELL"):
            value = result.get("value_usdt", 0.0)
            usdt_bal = balances.setdefault(
                "USDT", {"free": 0.0, "locked": 0.0, "total": 0.0, "value_usdt": 0.0}
            )
            if action == "BUY":
                usdt_bal["free"] = max(0.0, usdt_bal.get("free", 0.0) - value)
            else:
                usdt_bal["free"] = usdt_bal.get("free", 0.0) + value
            trades_done += 1

    log.info(f"Cycle complete — {trades_done} trade(s) executed out of {len(trades_list)} planned")
    return results
