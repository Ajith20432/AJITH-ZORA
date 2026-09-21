"""
data.py — market data access via ccxt
"""
from __future__ import annotations

import time

import ccxt
import pandas as pd

from config import CFG


def get_exchange() -> ccxt.Exchange:
    ex_class = getattr(ccxt, CFG.exchange_id)
    params = {"enableRateLimit": True}
    if CFG.live_allowed():
        params["apiKey"] = CFG.api_key
        params["secret"] = CFG.api_secret
    return ex_class(params)


def fetch_ohlcv_df(
    exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 300, retries: int = 3
) -> pd.DataFrame:
    """Fetch OHLCV candles as a pandas DataFrame, with basic retry."""
    last_err = None
    for _ in range(retries):
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            return df
        except Exception as e:  # network hiccups, rate limits, etc.
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"Failed to fetch OHLCV for {symbol} {timeframe}: {last_err}")


def fetch_multi_timeframe(
    exchange: ccxt.Exchange, symbol: str, timeframes: tuple[str, ...]
) -> dict[str, pd.DataFrame]:
    """Return {timeframe: DataFrame} for every requested timeframe."""
    return {tf: fetch_ohlcv_df(exchange, symbol, tf) for tf in timeframes}
