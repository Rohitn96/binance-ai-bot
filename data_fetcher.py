import math
import time
import xml.etree.ElementTree as ET
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DISPLAY_NAMES = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOLUSDT": "SOL/USDT",
    "BNBUSDT": "BNB/USDT",
}

TESTNET_REST = "https://testnet.binance.vision"
FNG_URL = "https://api.alternative.me/fng/?limit=3"
NEWS_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

MAX_COINS_HELD = 5
USDT_RESERVE_MIN_PCT = 15
MAX_TRADES_PER_CYCLE = 3
TOP_COINS_TO_SCAN = 20


def _build_client() -> Client:
    client = Client(
        api_key=config.BINANCE_API_KEY,
        api_secret=config.BINANCE_SECRET_KEY,
        testnet=(config.TRADING_MODE == "testnet"),
    )
    if config.TRADING_MODE == "testnet":
        client.API_URL = f"{TESTNET_REST}/api"
    return client


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den_x = math.sqrt(sum((v - mx) ** 2 for v in x))
    den_y = math.sqrt(sum((v - my) ** 2 for v in y))
    if den_x == 0 or den_y == 0:
        return 0.0
    return round(num / (den_x * den_y), 4)


def fetch_market_data(client: Client, extra_symbols: list[str] | None = None) -> dict:
    """24h ticker stats and last 50 1h candles for each coin."""
    symbols = list(COINS)
    if extra_symbols:
        symbols = list(dict.fromkeys(symbols + extra_symbols))

    tickers = {t["symbol"]: t for t in client.get_ticker()}
    market = {}

    for symbol in symbols:
        ticker = tickers.get(symbol, {})
        klines = client.get_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_1HOUR,
            limit=50,
        )
        candles = [
            {
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
            }
            for k in klines
        ]
        market[symbol] = {
            "display": DISPLAY_NAMES.get(symbol, symbol),
            "price": float(ticker.get("lastPrice", 0)),
            "change_24h_pct": float(ticker.get("priceChangePercent", 0)),
            "high_24h": float(ticker.get("highPrice", 0)),
            "low_24h": float(ticker.get("lowPrice", 0)),
            "volume_24h": float(ticker.get("volume", 0)),
            "candles": candles,
        }

    return market


def fetch_portfolio(client: Client) -> dict:
    """All non-zero balances and total USDT value."""
    account = client.get_account()
    balances_raw = account.get("balances", [])

    prices: dict[str, float] = {}
    for item in client.get_ticker():
        prices[item["symbol"]] = float(item["lastPrice"])

    balances = {}
    total_usdt = 0.0

    for b in balances_raw:
        asset = b["asset"]
        free = float(b["free"])
        locked = float(b["locked"])
        total = free + locked
        if total <= 0:
            continue

        if asset == "USDT":
            value_usdt = total
        else:
            pair = f"{asset}USDT"
            price = prices.get(pair, 0.0)
            value_usdt = total * price

        balances[asset] = {
            "free": free,
            "locked": locked,
            "total": total,
            "value_usdt": value_usdt,
        }
        total_usdt += value_usdt

    for asset, data in balances.items():
        data["portfolio_pct"] = (
            round(data["value_usdt"] / total_usdt * 100, 2) if total_usdt > 0 else 0.0
        )

    return {
        "balances": balances,
        "total_usdt": round(total_usdt, 2),
    }


def fetch_fear_and_greed() -> dict:
    """Fear & Greed Index — last 3 days."""
    try:
        resp = requests.get(FNG_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        entries = [
            {
                "value": int(d["value"]),
                "label": d["value_classification"],
                "timestamp": int(d["timestamp"]),
            }
            for d in data[:3]
        ]
        return {
            "current_value": entries[0]["value"] if entries else None,
            "current_label": entries[0]["label"] if entries else None,
            "history": entries,
        }
    except Exception as exc:
        return {"error": str(exc), "current_value": None, "current_label": None, "history": []}


def fetch_news() -> list[dict]:
    """Top 8 most recent crypto news headlines from CoinDesk RSS (no auth required)."""
    try:
        resp = requests.get(NEWS_RSS_URL, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = root.findall("./channel/item")
        results = []
        for item in items[:8]:
            results.append(
                {
                    "title": item.findtext("title", "").strip(),
                    "source": "CoinDesk",
                    "published_at": item.findtext("pubDate", "").strip(),
                    "url": item.findtext("link", "").strip(),
                }
            )
        return results
    except Exception as exc:
        return [{"error": str(exc)}]


def get_coingecko_top20() -> list[dict]:
    """Top 20 coins by market cap with multi-timeframe price change data."""
    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 20,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "1h,24h,7d",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {
                "id": c["id"],
                "symbol": c["symbol"],
                "name": c["name"],
                "current_price": c["current_price"],
                "price_change_percentage_1h_in_currency": c.get(
                    "price_change_percentage_1h_in_currency"
                ),
                "price_change_percentage_24h_in_currency": c.get(
                    "price_change_percentage_24h_in_currency"
                ),
                "price_change_percentage_7d_in_currency": c.get(
                    "price_change_percentage_7d_in_currency"
                ),
                "total_volume": c["total_volume"],
                "market_cap": c["market_cap"],
                "market_cap_rank": c["market_cap_rank"],
            }
            for c in resp.json()
        ]
    except Exception:
        return []


def get_global_market_data(top20: list[dict] | None = None) -> dict:
    """Global crypto market stats. Pass top20 to include breadth metrics."""
    try:
        resp = requests.get(f"{COINGECKO_BASE}/global", timeout=10)
        resp.raise_for_status()
        d = resp.json().get("data", {})

        mc = d.get("total_market_cap", {})
        vol = d.get("total_volume", {})
        dom = d.get("market_cap_percentage", {})

        coins_gaining = 0
        if top20:
            coins_gaining = sum(
                1
                for c in top20
                if (c.get("price_change_percentage_24h_in_currency") or 0) > 0
            )

        return {
            "total_market_cap_usd": mc.get("usd"),
            "total_volume_usd": vol.get("usd"),
            "market_cap_change_percentage_24h": d.get("market_cap_change_percentage_24h_usd"),
            "btc_dominance": dom.get("btc"),
            "eth_dominance": dom.get("eth"),
            "active_cryptocurrencies": d.get("active_cryptocurrencies"),
            "coins_gaining": coins_gaining,
            "market_breadth": round(coins_gaining / TOP_COINS_TO_SCAN * 100, 1),
        }
    except Exception:
        return {}


def get_trending_coins() -> list[str]:
    """Top 7 trending coin symbols (lowercase) from CoinGecko."""
    try:
        resp = requests.get(f"{COINGECKO_BASE}/search/trending", timeout=10)
        resp.raise_for_status()
        coins = resp.json().get("coins", [])
        return [c["item"]["symbol"].lower() for c in coins[:7]]
    except Exception:
        return []


def get_order_book_pressure(symbol: str) -> dict:
    """Buy/sell pressure from top 20 bids and asks on Binance Testnet."""
    try:
        client = _build_client()
        depth = client.get_order_book(symbol=symbol, limit=20)
        total_bid = sum(float(b[1]) for b in depth["bids"])
        total_ask = sum(float(a[1]) for a in depth["asks"])
        if total_ask == 0:
            ratio = 0.0
        else:
            ratio = round(total_bid / total_ask, 4)

        if ratio > 1.5:
            label = "STRONG_BUY"
        elif ratio > 1.1:
            label = "BUY"
        elif ratio >= 0.9:
            label = "NEUTRAL"
        elif ratio >= 0.7:
            label = "SELL"
        else:
            label = "STRONG_SELL"

        return {
            "total_bid_volume": round(total_bid, 4),
            "total_ask_volume": round(total_ask, 4),
            "pressure_ratio": ratio,
            "pressure_label": label,
        }
    except Exception:
        return {"pressure_ratio": 0.0, "pressure_label": "NEUTRAL"}


def get_binance_top20_symbols(coingecko_top20: list[dict]) -> list[str]:
    """Map top 20 CoinGecko coins to valid Binance Testnet USDT trading pairs."""
    try:
        client = _build_client()
        exchange_info = client.get_exchange_info()
        valid_symbols = {s["symbol"] for s in exchange_info["symbols"] if s["status"] == "TRADING"}
    except Exception:
        valid_symbols = set()

    result = []
    for coin in coingecko_top20:
        binance_symbol = coin["symbol"].upper() + "USDT"
        if binance_symbol in valid_symbols:
            result.append(binance_symbol)

    return result


def get_cross_correlation(symbols: list[str]) -> dict[str, float]:
    """Pearson correlation of each coin's hourly returns vs BTC over last 20 candles."""
    try:
        client = _build_client()
        fetch_set = list(dict.fromkeys(["BTCUSDT"] + symbols))[:6]

        returns: dict[str, list[float]] = {}
        for symbol in fetch_set:
            try:
                klines = client.get_klines(
                    symbol=symbol,
                    interval=Client.KLINE_INTERVAL_1HOUR,
                    limit=21,
                )
                closes = [float(k[4]) for k in klines]
                if len(closes) >= 2:
                    returns[symbol] = [
                        (closes[i] - closes[i - 1]) / closes[i - 1]
                        for i in range(1, len(closes))
                    ]
            except Exception:
                pass

        btc_returns = returns.get("BTCUSDT", [])
        if not btc_returns:
            return {}

        correlation: dict[str, float] = {}
        for symbol in symbols:
            if symbol == "BTCUSDT":
                correlation[symbol] = 1.0
                continue
            alt_returns = returns.get(symbol, [])
            n = min(len(btc_returns), len(alt_returns))
            if n < 2:
                correlation[symbol] = 0.0
            else:
                correlation[symbol] = _pearson(btc_returns[:n], alt_returns[:n])

        return correlation
    except Exception:
        return {}


def fetch_all() -> dict:
    """Fetch all data sources and return a combined package."""
    client = _build_client()

    portfolio = fetch_portfolio(client)
    top20 = get_coingecko_top20()
    binance_top20 = get_binance_top20_symbols(top20)

    # Include market data for any top-20 coin we currently hold beyond the default COINS
    held_assets = set(portfolio["balances"].keys()) - {"USDT"}
    held_top20_symbols = [
        f"{asset}USDT"
        for asset in held_assets
        if f"{asset}USDT" in binance_top20 and f"{asset}USDT" not in COINS
    ]
    market_data = fetch_market_data(client, extra_symbols=held_top20_symbols)

    fear_greed = fetch_fear_and_greed()
    news = fetch_news()
    global_market = get_global_market_data(top20)
    trending = get_trending_coins()

    top5_symbols = binance_top20[:5]
    order_book = {symbol: get_order_book_pressure(symbol) for symbol in top5_symbols}
    correlation = get_cross_correlation(top5_symbols)

    return {
        "timestamp": int(time.time()),
        "market": market_data,
        "portfolio": portfolio,
        "fear_and_greed": fear_greed,
        "news": news,
        "coingecko_top20": top20,
        "global_market": global_market,
        "trending_coins": trending,
        "order_book": order_book,
        "correlation": correlation,
    }
