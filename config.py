"""
config.py — loads settings from .env
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    exchange_id: str = os.getenv("EXCHANGE_ID", "htx")
    symbol: str = os.getenv("SYMBOL", "BTC/USDT")          # used by backtest/learn by default
    symbols: tuple[str, ...] = field(default_factory=lambda: tuple(
        s.strip() for s in os.getenv("SYMBOLS", os.getenv("SYMBOL", "BTC/USDT")).split(",") if s.strip()
    ))                                                       # traded together by `run` / `live`
    timeframes: tuple[str, ...] = field(default_factory=lambda: tuple(
        os.getenv("TIMEFRAMES", "15m,1h,4h").split(",")
    ))

    # capital / risk
    starting_capital: float = _f("STARTING_CAPITAL", 1000)
    risk_per_trade_pct: float = _f("RISK_PER_TRADE_PCT", 0.5)     # % of equity risked per trade
    max_exposure_pct: float = _f("MAX_EXPOSURE_PCT", 30)          # % of equity allowed in open positions
    max_daily_loss_pct: float = _f("MAX_DAILY_LOSS_PCT", 3)       # kill switch
    max_drawdown_pct: float = _f("MAX_DRAWDOWN_PCT", 15)          # kill switch from peak equity
    atr_stop_mult: float = _f("ATR_STOP_MULT", 2.0)
    atr_take_mult: float = _f("ATR_TAKE_MULT", 3.5)

    # loop
    poll_seconds: int = _i("POLL_SECONDS", 60)

    # mode — LIVE_TRADING must be explicitly and deliberately enabled.
    live_trading: bool = _b("LIVE_TRADING", False)
    i_understand_the_risk: bool = _b("I_UNDERSTAND_THE_RISK", False)

    # exchange API (only needed for LIVE_TRADING)
    api_key: str = os.getenv("EXCHANGE_API_KEY", "")
    api_secret: str = os.getenv("EXCHANGE_API_SECRET", "")

    # ---- Multi-AI expert panel (all optional, advisory-only — see multi_ai.py) ----
    # Leave any key blank to skip that provider entirely; nothing breaks.
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openrouter/free")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    # optional paid extras — off unless keyed
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # judge: which provider synthesizes the individual AI verdicts into one call.
    # Blank = auto-pick whichever configured provider answers first each cycle.
    ai_judge_provider: str = os.getenv("AI_JUDGE_PROVIDER", "")
    # agentic tool use: lets Anthropic/Groq/OpenRouter/OpenAI call get_price_series
    # themselves before answering. Off saves tokens/latency at the cost of less context.
    ai_tool_use_enabled: bool = _b("AI_TOOL_USE_ENABLED", True)

    db_path: str = os.getenv("DB_PATH", "zora.db")

    # ---- Telegram alerts + remote control (both optional) ----
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    def live_allowed(self) -> bool:
        return self.live_trading and self.i_understand_the_risk and bool(self.api_key) and bool(self.api_secret)


CFG = Config()
