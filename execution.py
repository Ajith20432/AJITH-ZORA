"""
execution.py — the only module that can move real money.

PaperExecutor simulates fills at the price already fetched this cycle — no
network calls beyond market data, no real money, always available.

LiveExecutor places real orders through ccxt. It is only ever constructed
after CFG.live_allowed() (LIVE_TRADING + I_UNDERSTAND_THE_RISK + API keys,
all set in .env) AND the typed runtime confirmation in bot.py's live()
function have both passed — nothing in this file re-checks that gate,
which is precisely why callers must never construct a LiveExecutor without
going through that path first.

Design choices for LiveExecutor, and why:
- Amount is rounded to the exchange's precision and checked against its
  minimum order size before sending; trades below the minimum are skipped
  and alerted, never silently upsized to meet the minimum.
- A retry is attempted ONLY for errors that happen before any response is
  received from the exchange (ccxt.NetworkError) — never after a response,
  however ambiguous, because retrying an order whose actual outcome is
  unknown risks placing it twice. Anything else (insufficient funds,
  rejected params, etc.) is reported and left for manual review.
- A client order ID is attached so that if a retry DOES happen, exchanges
  that support idempotency keys can recognize a duplicate rather than
  filling it twice. Not all exchanges honor this — it reduces the risk,
  it does not eliminate it.
- Stops and take-profits here are software-monitored (the trading loop
  checks price against them each cycle and fires a closing market order),
  not native exchange bracket/OCO orders — behavior varies too much across
  exchanges to hardcode one approach. This means: if the bot crashes or
  loses connectivity while a position is open, that position has NO
  protection until the bot is running again. Consider also placing a
  manual stop-loss order on the exchange itself as a backup while testing
  this, and start with small size.
"""
from __future__ import annotations

import time

import ccxt


class PaperExecutor:
    live = False

    def open(self, exchange: ccxt.Exchange, symbol: str, action: str, amount: float, price: float) -> float:
        return price  # assume a perfect fill at the last observed price

    def close(self, exchange: ccxt.Exchange, symbol: str, action: str, amount: float, price: float) -> float:
        return price


class LiveExecutor:
    live = True

    def __init__(self, telegram_alert=lambda msg: None) -> None:
        self.alert = telegram_alert

    def _sized_amount(self, exchange: ccxt.Exchange, symbol: str, amount: float) -> float | None:
        try:
            market = exchange.market(symbol)
            amount = float(exchange.amount_to_precision(symbol, amount))
            min_amount = ((market.get("limits", {}) or {}).get("amount", {}) or {}).get("min")
            if min_amount and amount < min_amount:
                return None
            return amount
        except Exception:
            return amount  # best-effort — the exchange will reject it if truly invalid

    def _place(self, exchange: ccxt.Exchange, symbol: str, side: str, amount: float) -> dict | None:
        amount = self._sized_amount(exchange, symbol, amount)
        if not amount:
            self.alert(f"[live] {symbol} {side} skipped — computed size is below the exchange minimum")
            return None

        params = {"clientOrderId": f"zora-{int(time.time() * 1000)}"}

        attempts = 0
        while attempts < 2:
            attempts += 1
            try:
                return exchange.create_order(symbol, "market", side, amount, None, params)
            except ccxt.NetworkError as e:
                if attempts >= 2:
                    self.alert(f"[live] {symbol} {side} order FAILED after retry (network): {e}. "
                               f"Check your exchange account manually — do not assume it didn't go through.")
                    return None
                time.sleep(2)
            except ccxt.BaseError as e:
                # Anything past a pure network error (insufficient funds, invalid params,
                # exchange-side rejection) is not safe to blindly retry.
                self.alert(f"[live] {symbol} {side} order rejected: {e}")
                return None
        return None

    def open(self, exchange: ccxt.Exchange, symbol: str, action: str, amount: float, price: float) -> float | None:
        side = "buy" if action == "BUY" else "sell"
        order = self._place(exchange, symbol, side, amount)
        if order is None:
            return None
        fill_price = order.get("average") or order.get("price") or price
        self.alert(f"[live] OPENED {action} {symbol} amount={amount} @ ~{fill_price}")
        return fill_price

    def close(self, exchange: ccxt.Exchange, symbol: str, action: str, amount: float, price: float) -> float | None:
        side = "sell" if action == "BUY" else "buy"  # closing a BUY = sell; closing a SELL = buy back
        order = self._place(exchange, symbol, side, amount)
        if order is None:
            return None
        fill_price = order.get("average") or order.get("price") or price
        self.alert(f"[live] CLOSED {action} {symbol} amount={amount} @ ~{fill_price}")
        return fill_price
