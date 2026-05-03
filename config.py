import os
from dotenv import load_dotenv

load_dotenv()

_REQUIRED = [
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "STARTING_CAPITAL",
    "STOP_LOSS_PERCENT",
    "DRY_RUN",
    "TRADING_MODE",
]

_missing = [k for k in _REQUIRED if not os.getenv(k)]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Ensure they are set in your .env file."
    )

BINANCE_API_KEY: str = os.environ["BINANCE_API_KEY"]
BINANCE_SECRET_KEY: str = os.environ["BINANCE_SECRET_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

STARTING_CAPITAL: float = float(os.environ["STARTING_CAPITAL"])
STOP_LOSS_PERCENT: float = float(os.environ["STOP_LOSS_PERCENT"])

_dry_run_raw = os.environ["DRY_RUN"].strip().lower()
if _dry_run_raw not in ("true", "false", "1", "0"):
    raise ValueError(f"DRY_RUN must be 'true' or 'false', got: {os.environ['DRY_RUN']!r}")
DRY_RUN: bool = _dry_run_raw in ("true", "1")

TRADING_MODE: str = os.environ["TRADING_MODE"].strip().lower()
if TRADING_MODE not in ("testnet", "live"):
    raise ValueError(f"TRADING_MODE must be 'testnet' or 'live', got: {os.environ['TRADING_MODE']!r}")

TRADE_INTERVAL_MINUTES: int = int(os.getenv("TRADE_INTERVAL_MINUTES", "5"))
MAX_TRADE_PERCENT: float = float(os.getenv("MAX_TRADE_PERCENT", "20"))
MIN_USDT_RESERVE: float = float(os.getenv("MIN_USDT_RESERVE", "20"))

# Dynamic top-20 trading system settings
MAX_COINS_HELD: int = 5
MIN_USDT_RESERVE_PCT: float = 2.0
MAX_TRADES_PER_CYCLE: int = 2
TOP_COINS_TO_SCAN: int = 20

# Optional — bot runs fine without these; Telegram messages are silently skipped
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
