# Binance AI Trading Bot

An autonomous cryptocurrency trading bot that uses **Claude AI (Haiku)** to make real-time BUY/SELL/HOLD decisions across the top 20 cryptocurrencies. Runs 24/7 on AWS EC2, executes trades on Binance, and reports performance via Telegram and a live Next.js dashboard.

> ⚠️ Built and tested on **Binance Testnet** only. Not financial advice. Use at your own risk in live markets.

---

## What it does

- Scans the top 20 coins by market cap every 30 minutes via CoinGecko
- Fetches live price data, order book depth, and candlestick data from Binance
- Computes 6 technical indicators per coin: RSI, MACD, Bollinger Bands, EMA, SMA, Volume — each scored 0–100
- Pulls the Fear & Greed Index and live crypto news headlines
- Sends the full market context to Claude AI, which returns a BUY/SELL/HOLD decision with confidence score and reasoning
- Applies safety filters before any execution: score threshold, RSI cap, stablecoin exclusion, 50% portfolio stop-loss
- Executes orders via the Binance REST API
- Sends trade alerts and daily performance reports via Telegram
- Exposes a Flask REST API (6 endpoints) consumed by a live Next.js dashboard on Vercel

---

## Results (Testnet)

| Metric | Value |
|---|---|
| Runtime | 4+ days continuous |
| Trades executed | 270+ |
| Coins traded | BTC, ETH, SOL, TON, LINK, ZEC |
| Peak portfolio value | $404,645 (+3.76%) |
| Infrastructure | AWS EC2 t3.micro, Ubuntu 24.04 |

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| AI / LLM | Anthropic Claude API (claude-haiku-4-5) |
| Exchange | Binance API (python-binance) |
| Market data | CoinGecko API, alternative.me, CoinDesk |
| Technical analysis | pandas, numpy |
| Backend API | Flask, flask-cors |
| Notifications | Telegram Bot API |
| Infrastructure | AWS EC2 (t3.micro, Ubuntu 24.04) |
| Process management | systemd |
| Dashboard frontend | Next.js 14, TypeScript, Tailwind CSS, Recharts |
| Dashboard hosting | Vercel |
| Version control | GitHub |

---

## Project structure

```
binance-ai-bot/
├── main.py                 ← entry point, main trading loop
├── ai_engine.py            ← Claude AI decision logic
├── data_fetcher.py         ← Binance + CoinGecko data ingestion
├── indicators.py           ← RSI, MACD, Bollinger Bands, EMA, SMA, Volume
├── executor.py             ← order execution via Binance API
├── risk_manager.py         ← safety filters and stop-loss logic
├── api_server.py           ← Flask REST API for dashboard
├── telegram_notifier.py    ← trade alerts and daily reports
├── logger.py               ← structured logging
├── config.py               ← environment variable loader
├── bot.service             ← systemd service file for AWS EC2
└── requirements.txt
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Rohitn96/binance-ai-bot.git
cd binance-ai-bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create your `.env` file

```env
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET_KEY=your_binance_secret_key
ANTHROPIC_API_KEY=your_anthropic_api_key
STARTING_CAPITAL=1000
STOP_LOSS_PERCENT=50
DRY_RUN=true
TRADING_MODE=testnet

# Optional — bot runs without these; Telegram alerts are silently skipped
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

> Start with `DRY_RUN=true` and `TRADING_MODE=testnet` to test without real money.

### 3. Run locally

```bash
python main.py
```

### 4. Deploy to AWS EC2 (optional)

Copy the `bot.service` systemd file to `/etc/systemd/system/`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable bot
sudo systemctl start bot
sudo systemctl status bot
```

---

## How the AI decision works

Each trading cycle, the bot sends Claude AI a prompt containing:

- Current portfolio state and USDT balance
- For each of the top 20 coins: price, volume, order book snapshot, candlestick data
- Computed indicator scores (RSI, MACD, Bollinger Bands, EMA, SMA) — each normalised 0–100
- Fear & Greed Index value
- Recent news headlines for each coin

Claude responds with a structured decision:

```json
{
  "action": "BUY",
  "symbol": "SOLUSDT",
  "confidence": 78,
  "reasoning": "RSI at 38 signals oversold conditions. MACD crossover forming. Fear index at 22 (extreme fear) historically precedes rebounds."
}
```

Before execution, the risk manager validates: minimum confidence threshold, RSI cap, no stablecoin trades, and portfolio stop-loss check.

---

## Safety features

- **Testnet mode** — test with fake money before going live
- **Dry run mode** — simulates decisions without placing any orders
- **Score threshold** — only executes trades above a minimum confidence score
- **RSI cap** — blocks buys when RSI signals overbought
- **Stablecoin exclusion** — never trades USDT, USDC, BUSD
- **50% stop-loss** — halts all trading if portfolio drops 50% from start
- **USDT reserve** — always keeps a minimum USDT buffer

---

## Dashboard

A separate Next.js frontend consumes the Flask API and displays:

- Live portfolio value and holdings
- Trade history with AI reasoning per trade
- Market snapshot across top 20 coins
- Performance chart over time

---

## Author

**Rohit Nair** — [github.com/Rohitn96](https://github.com/Rohitn96) · [linkedin.com/in/rohitn96](https://linkedin.com/in/rohitn96)
