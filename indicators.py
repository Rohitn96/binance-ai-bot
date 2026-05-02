from __future__ import annotations
import math
from typing import Any


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average using Wilder-style smoothing (multiplier = 2/(period+1))."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result: list[float] = []
    seed = sum(values[:period]) / period
    result.append(seed)
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _sma(values: list[float], period: int) -> list[float]:
    return [
        sum(values[i : i + period]) / period
        for i in range(len(values) - period + 1)
    ]


def _stddev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI using Wilder's smoothed average (standard definition)."""
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [abs(min(c, 0.0)) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calculate(symbol: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Given 50 hourly candles (dicts with 'close' and 'volume' keys),
    return a summary dict of all indicators for that coin.
    """
    if len(candles) < 26:
        return {"symbol": symbol, "error": "insufficient candle data"}

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # --- RSI(14) ---
    rsi_val = _rsi(closes, 14)
    rsi_signal = "NEUTRAL"
    if rsi_val is not None:
        if rsi_val < 30:
            rsi_signal = "OVERSOLD"
        elif rsi_val > 70:
            rsi_signal = "OVERBOUGHT"

    # --- SMA(20) ---
    sma20_series = _sma(closes, 20)
    sma20 = round(sma20_series[-1], 6) if sma20_series else None

    # --- EMA(12) and EMA(26) ---
    ema12_series = _ema(closes, 12)
    ema26_series = _ema(closes, 26)
    ema12 = round(ema12_series[-1], 6) if ema12_series else None
    ema26 = round(ema26_series[-1], 6) if ema26_series else None

    # --- MACD ---
    macd_line: float | None = None
    macd_signal_line: float | None = None
    macd_histogram: float | None = None
    macd_crossover: str = "NEUTRAL"

    if ema12_series and ema26_series:
        overlap = min(len(ema12_series), len(ema26_series))
        ema12_aligned = ema12_series[-overlap:]
        ema26_aligned = ema26_series[-overlap:]
        macd_series = [e12 - e26 for e12, e26 in zip(ema12_aligned, ema26_aligned)]
        macd_line = round(macd_series[-1], 6)

        if len(macd_series) >= 9:
            signal_series = _ema(macd_series, 9)
            if signal_series:
                macd_signal_line = round(signal_series[-1], 6)
                macd_histogram = round(macd_line - macd_signal_line, 6)
                if len(signal_series) >= 2 and len(macd_series) >= 2:
                    prev_macd = macd_series[-2]
                    prev_signal = signal_series[-2]
                    if prev_macd < prev_signal and macd_line > macd_signal_line:
                        macd_crossover = "BULLISH_CROSS"
                    elif prev_macd > prev_signal and macd_line < macd_signal_line:
                        macd_crossover = "BEARISH_CROSS"
                    elif macd_line > macd_signal_line:
                        macd_crossover = "BULLISH"
                    else:
                        macd_crossover = "BEARISH"

    # --- Bollinger Bands(20) ---
    bb_upper = bb_lower = bb_middle = bb_width = None
    bb_position = "INSIDE"

    if sma20_series:
        bb_middle = sma20
        window = closes[-20:]
        std = _stddev(window)
        bb_upper = round(bb_middle + 2 * std, 6)
        bb_lower = round(bb_middle - 2 * std, 6)
        bb_width = round(bb_upper - bb_lower, 6)
        current_price = closes[-1]
        if current_price > bb_upper:
            bb_position = "ABOVE_UPPER"
        elif current_price < bb_lower:
            bb_position = "BELOW_LOWER"

    # --- Volume trend ---
    vol_sma20 = _sma(volumes, 20)
    vol_avg = vol_sma20[-1] if vol_sma20 else None
    current_vol = volumes[-1]
    vol_ratio = round(current_vol / vol_avg, 3) if vol_avg else None
    vol_trend = "AVERAGE"
    if vol_ratio is not None:
        if vol_ratio > 1.5:
            vol_trend = "HIGH"
        elif vol_ratio < 0.5:
            vol_trend = "LOW"

    return {
        "symbol": symbol,
        "current_price": closes[-1],
        "rsi": {
            "value": rsi_val,
            "signal": rsi_signal,
        },
        "sma20": sma20,
        "ema12": ema12,
        "ema26": ema26,
        "macd": {
            "line": macd_line,
            "signal": macd_signal_line,
            "histogram": macd_histogram,
            "crossover": macd_crossover,
        },
        "bollinger_bands": {
            "upper": bb_upper,
            "middle": bb_middle,
            "lower": bb_lower,
            "width": bb_width,
            "position": bb_position,
        },
        "volume": {
            "current": round(current_vol, 2),
            "avg_20": round(vol_avg, 2) if vol_avg else None,
            "ratio_vs_avg": vol_ratio,
            "trend": vol_trend,
        },
    }


def calculate_all(market_data: dict) -> dict[str, dict]:
    """Run calculate() for every coin in the market data package."""
    results = {}
    for symbol, data in market_data.items():
        candles = data.get("candles", [])
        results[symbol] = calculate(symbol, candles)
    return results


def score_coin(symbol: str, candles: list[dict[str, Any]], volume_avg: float) -> int:  # noqa: ARG001
    """Momentum score 0-100: RSI(25) + MACD(25) + Volume(25) + MA(25)."""
    if len(candles) < 26:
        return 0

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    score = 0

    # RSI score
    rsi_val = _rsi(closes, 14)
    if rsi_val is not None:
        if 55 <= rsi_val <= 65:
            score += 25
        elif 65 < rsi_val <= 70:
            score += 20
        elif 30 <= rsi_val <= 35:
            score += 20

    # MACD score — bullish crossover only
    ema12_s = _ema(closes, 12)
    ema26_s = _ema(closes, 26)
    if ema12_s and ema26_s:
        overlap = min(len(ema12_s), len(ema26_s))
        macd_s = [e12 - e26 for e12, e26 in zip(ema12_s[-overlap:], ema26_s[-overlap:])]
        if len(macd_s) >= 9:
            sig_s = _ema(macd_s, 9)
            if sig_s and len(sig_s) >= 2 and len(macd_s) >= 2:
                if macd_s[-2] < sig_s[-2] and macd_s[-1] > sig_s[-1]:
                    score += 25

    # Volume score
    current_vol = volumes[-1] if volumes else 0.0
    if volume_avg > 0:
        ratio = current_vol / volume_avg
        if ratio > 1.5:
            score += 25
        elif ratio > 1.2:
            score += 15

    # MA score
    sma20_s = _sma(closes, 20)
    if sma20_s and closes[-1] > sma20_s[-1]:
        score += 25

    return min(score, 100)


def rank_top20(
    coingecko_top20: list[dict[str, Any]],
    trending_coins: list[str],
    candles: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Score and rank top 20 CoinGecko coins by trading opportunity."""
    trending_set = set(trending_coins)
    ranked = []

    for coin in coingecko_top20:
        cg_symbol = coin["symbol"].lower()
        binance_symbol = cg_symbol.upper() + "USDT"
        coin_candles = candles.get(binance_symbol, [])

        vol_avg = 0.0
        if len(coin_candles) >= 20:
            vol_avg = sum(c["volume"] for c in coin_candles[-20:]) / 20

        momentum = score_coin(binance_symbol, coin_candles, vol_avg) if coin_candles else 0

        is_trending = cg_symbol in trending_set
        if is_trending:
            momentum += 15

        ch_1h = coin.get("price_change_percentage_1h_in_currency") or 0.0
        ch_24h = coin.get("price_change_percentage_24h_in_currency") or 0.0
        ch_7d = coin.get("price_change_percentage_7d_in_currency") or 0.0

        if ch_1h > 0 and ch_24h > 0:
            momentum += 10
        if ch_7d < 0:
            momentum -= 10

        ranked.append({
            "symbol": binance_symbol,
            "cg_symbol": cg_symbol,
            "name": coin["name"],
            "score": momentum,
            "current_price": coin["current_price"],
            "change_1h": ch_1h,
            "change_24h": ch_24h,
            "change_7d": ch_7d,
            "market_cap_rank": coin["market_cap_rank"],
            "is_trending": is_trending,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked
