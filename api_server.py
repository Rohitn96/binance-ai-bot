import json
import os
import re
from datetime import datetime, timezone

from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_STATUS_FILE = os.path.join(_LOG_DIR, "status.json")
_TRADES_LOG = os.path.join(_LOG_DIR, "trades.log")

_LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| \w+\s*\| (.*)$")


def _read_status() -> dict:
    try:
        with open(_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _parse_trades_log() -> list[dict]:
    if not os.path.isfile(_TRADES_LOG):
        return []

    cycles: list[dict] = []
    current: dict = {}

    with open(_TRADES_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for raw in lines:
        m = _LOG_LINE_RE.match(raw.rstrip("\n"))
        if not m:
            continue
        msg = m.group(1).strip()

        if msg.startswith("CYCLE  "):
            if current.get("timestamp"):
                cycles.append(current)
            current = {"timestamp": msg[len("CYCLE  "):].strip()}
        elif msg.startswith("Action     :"):
            current["action"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("Coin       :"):
            current["coin"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("Confidence :"):
            current["confidence"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("Reason     :"):
            current["reason"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("Status     :"):
            current["status"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("After  :"):
            try:
                val_str = msg.split("$", 1)[1].split()[0].replace(",", "")
                current["portfolio_value"] = float(val_str)
            except (IndexError, ValueError):
                pass

    if current.get("timestamp"):
        cycles.append(current)

    return cycles


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/status")
def status():
    try:
        data = _read_status()
        if not data:
            return jsonify({"bot_running": False})
        return jsonify(data)
    except Exception:
        return jsonify({"bot_running": False})


@app.route("/portfolio")
def portfolio():
    try:
        p = _read_status().get("portfolio", {})
        return jsonify({
            "total_value": p.get("total_value", 0),
            "pnl_dollar": p.get("pnl_dollar", 0),
            "pnl_percent": p.get("pnl_percent", 0),
            "usdt_available": p.get("usdt_available", 0),
            "top_holdings": p.get("top_holdings", []),
        })
    except Exception:
        return jsonify({
            "total_value": 0,
            "pnl_dollar": 0,
            "pnl_percent": 0,
            "usdt_available": 0,
            "top_holdings": [],
        })


@app.route("/market")
def market():
    try:
        m = _read_status().get("market", {})
        return jsonify({
            "fear_greed_score": m.get("fear_greed_score"),
            "fear_greed_label": m.get("fear_greed_label"),
            "top_opportunity": m.get("top_opportunity"),
            "btc_price": m.get("btc_price"),
            "eth_price": m.get("eth_price"),
            "sol_price": m.get("sol_price"),
            "bnb_price": m.get("bnb_price"),
            "btc_rsi": m.get("btc_rsi"),
            "eth_rsi": m.get("eth_rsi"),
            "sol_rsi": m.get("sol_rsi"),
            "bnb_rsi": m.get("bnb_rsi"),
            "btc_macd": m.get("btc_macd"),
            "eth_macd": m.get("eth_macd"),
            "sol_macd": m.get("sol_macd"),
            "bnb_macd": m.get("bnb_macd"),
        })
    except Exception:
        return jsonify({})


@app.route("/decisions")
def decisions():
    try:
        cycles = _parse_trades_log()
        last50 = cycles[-50:]
        return jsonify([
            {
                "timestamp": c.get("timestamp"),
                "action": c.get("action"),
                "coin": c.get("coin"),
                "confidence": c.get("confidence"),
                "reason": c.get("reason"),
                "portfolio_value": c.get("portfolio_value"),
            }
            for c in reversed(last50)
        ])
    except Exception:
        return jsonify([])


@app.route("/trades")
def trades():
    try:
        cycles = _parse_trades_log()
        return jsonify([
            {
                "timestamp": c.get("timestamp"),
                "action": c.get("action"),
                "coin": c.get("coin"),
                "confidence": c.get("confidence"),
                "reason": c.get("reason"),
                "portfolio_value": c.get("portfolio_value"),
                "status": "executed",
            }
            for c in reversed(cycles)
            if c.get("status") == "executed"
        ])
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
