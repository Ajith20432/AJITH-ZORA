"""
bot.py — ZORA Advanced Pocket Trader (Termux edition)

Commands:
    python bot.py backtest [SYMBOL]   evaluate the strategy over history (brain untouched)
    python bot.py learn [SYMBOL]      replay history AND let the auto-learning brain update
    python bot.py brain               show the auto-learning brain's current learned weights
    python bot.py run                 paper-trading loop across every symbol in SYMBOLS —
                                       simulated fills, real market data, real AI/news calls
    python bot.py status              print current paper/live equity + trade summary
    python bot.py live                ONLY works if LIVE_TRADING + I_UNDERSTAND_THE_RISK + API
                                       keys are all set in .env, and asks for a typed
                                       confirmation before placing a single real order.

Default mode is PAPER. Nothing here places a real order unless you deliberately
opt in via .env AND type the confirmation phrase at runtime.

What each layer does, and does NOT do:
  - risk.py       hard position sizing / exposure / kill-switch gate. Nothing else
                   in this file can override it, in paper or live mode.
  - brain.py      auto-learning weights for how much to trust each expert
                   (15m / 1h / 4h / ai / news). Only changes which SIGNAL is
                   generated — never touches position sizing or the kill switch.
  - multi_ai.py   optional free/paid LLM panel, folded in as the "ai" expert.
  - news_feed.py  optional RSS keyword-sentiment, folded in as the "news" expert.
  - telegram_control.py   optional alerts + remote /pause /resume /kill /status /brain.
  - execution.py  the only place that can touch real money (LiveExecutor); PaperExecutor
                   simulates fills so `run` never places a real order.

`backtest` and `learn` always run technical-only, on a single symbol (SYMBOL,
or an override you pass on the command line) — there's no way to ask an LLM
or replay a news feed as it stood at a past candle, so those two commands
stay fast, free, and reproducible. `run` and `live` trade every symbol listed
in SYMBOLS (comma-separated in .env; defaults to just SYMBOL) and query the
AI/news layers for each one — more symbols means more API calls per cycle,
so watch free-tier rate limits and consider a longer POLL_SECONDS.

Everything operational is logged (console + rotating zora.log) via
logging_setup.py — see LOG_FILE / LOG_LEVEL in .env.
"""
from __future__ import annotations

import logging
import sys
import time

from logging_setup import configure_logging
from config import CFG
from data import get_exchange, fetch_multi_timeframe, fetch_ohlcv_df
from strategy import generate_signal
from risk import RiskEngine
from indicators import enrich
from brain import BRAIN
import multi_ai
import news_feed
import telegram_control
import execution
import db

logger = logging.getLogger("zora.bot")


def backtest(symbol: str | None = None, timeframe: str = "1h", candles: int = 500, learn: bool = False) -> None:
    symbol = symbol or CFG.symbol
    label = "learn" if learn else "backtest"
    logger.info("[%s] %s on %s, %d candles%s", label, symbol, timeframe, candles,
                " (updating the auto-learning brain)" if learn else " (brain untouched — pure evaluation)")
    exchange = get_exchange()
    df = fetch_ohlcv_df(exchange, symbol, timeframe, limit=candles)
    df = enrich(df)

    equity = CFG.starting_capital
    risk_engine = RiskEngine(equity)
    position: dict | None = None
    trades: list[float] = []
    before = BRAIN.snapshot() if learn else None

    window = 60
    for i in range(window, len(df) - 1):
        sub = df.iloc[: i + 1]
        signal = generate_signal({timeframe: sub})
        price = sub["close"].iloc[-1]
        atr_val = sub["atr14"].iloc[-1]

        if position:
            hit_stop = (position["action"] == "BUY" and price <= position["stop"]) or \
                       (position["action"] == "SELL" and price >= position["stop"])
            hit_take = (position["action"] == "BUY" and price >= position["take"]) or \
                       (position["action"] == "SELL" and price <= position["take"])
            if hit_stop or hit_take:
                direction = 1 if position["action"] == "BUY" else -1
                pnl = direction * (price - position["entry"]) * position["size"]
                equity += pnl
                risk_engine.update_equity(equity)
                risk_engine.remove_exposure(CFG.risk_per_trade_pct)
                trades.append(pnl)
                if learn:
                    BRAIN.update_from_trade(position["expert_scores"],
                                             position["factor_scores_by_tf"],
                                             position["action"], pnl)
                position = None

        if not position and not risk_engine.kill_switch_tripped:
            decision = risk_engine.evaluate(signal.action, price, atr_val)
            if decision.approved:
                risk_engine.add_exposure(CFG.risk_per_trade_pct)
                position = {
                    "action": signal.action,
                    "entry": price,
                    "size": decision.position_size,
                    "stop": decision.stop_loss,
                    "take": decision.take_profit,
                    "expert_scores": signal.expert_scores,
                    "factor_scores_by_tf": signal.factor_scores_by_tf,
                }

        if risk_engine.kill_switch_tripped:
            break

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    logger.info("Trades: %d  Wins: %d  Losses: %d", len(trades), len(wins), len(losses))
    logger.info("Final equity: %.2f  (start %.2f)", equity, CFG.starting_capital)
    if risk_engine.kill_switch_tripped:
        logger.warning("Kill switch tripped during this run (drawdown/daily loss limit).")

    if learn:
        after = BRAIN.snapshot()
        logger.info("Brain weights BEFORE: %s", before)
        logger.info("Brain weights AFTER : %s", after)
        logger.info("Total trades the brain has learned from so far: %d", after["trades_learned_from"])

    logger.info("Backtest/learn results depend heavily on the historical window, and fees/slippage "
                "are only approximated — treat this as a rough calibration tool, not a promise of "
                "future performance.")


def brain_status() -> None:
    s = BRAIN.snapshot()
    logger.info("Auto-learning brain — current weights (higher = more trusted right now)")
    logger.info("  Experts (15m/1h/4h/ai/news): %s", s["expert_weights"])
    logger.info("  Technical factors          : %s", s["factor_weights"])
    logger.info("  Trades learned from        : %d", s["trades_learned_from"])
    logger.info("These weights change only after a trade closes (paper, learn, or live) — "
                "they never affect position sizing or the risk engine's kill switches.")


def trade_loop(executor: execution.PaperExecutor | execution.LiveExecutor) -> None:
    """
    Shared trading loop for both paper and live mode — the only difference
    between them is which Executor is passed in. Everything else (risk
    gating, the auto-learning brain, the AI/news experts, Telegram control,
    multi-symbol handling) is identical, so paper mode is always an
    accurate rehearsal of what live mode would actually do.
    """
    symbols = CFG.symbols
    mode_label = "LIVE" if executor.live else "paper"

    # Alerts always go to the log (so nothing is silently lost if Telegram
    # isn't configured) AND to Telegram when it is.
    def alert(msg: str) -> None:
        logger.warning(msg)
        telegram_control.send(msg)

    if executor.live and isinstance(executor, execution.LiveExecutor):
        executor.alert = alert

    exchange = get_exchange()
    try:
        exchange.load_markets()
    except Exception as e:
        logger.warning("[%s] could not load exchange markets (%s); "
                        "order-size precision/minimum checks will be best-effort.", mode_label, e)

    conn = db.connect()
    equity = CFG.starting_capital
    risk_engine = RiskEngine(equity)
    positions: dict[str, dict | None] = {s: None for s in symbols}
    last_day = time.strftime("%Y-%m-%d")
    paused = False
    poller = telegram_control.CommandPoller()

    logger.info("[%s] Starting ZORA on %s. Ctrl+C to stop.", mode_label, ", ".join(symbols))
    logger.info("Risk per trade: %.2f%% | Max exposure: %.2f%% | Max drawdown kill switch: %.2f%%",
                CFG.risk_per_trade_pct, CFG.max_exposure_pct, CFG.max_drawdown_pct)
    logger.info("Auto-learning brain loaded (trades learned from so far: %d)", BRAIN.trades_learned_from)
    if telegram_control.enabled():
        logger.info("Telegram alerts + remote control enabled (/status /pause /resume /kill /brain)")
    telegram_control.send(f"ZORA {mode_label} trading started on {', '.join(symbols)}.")

    while True:
        try:
            for cmd in poller.poll():
                if cmd == "/pause":
                    paused = True
                    telegram_control.send("Paused — no new positions will open. Existing ones still managed.")
                elif cmd == "/resume":
                    paused = False
                    telegram_control.send("Resumed.")
                elif cmd == "/kill":
                    risk_engine.kill_switch_tripped = True
                    telegram_control.send("Kill switch manually tripped via Telegram. "
                                           "No new entries until you restart the bot.")
                elif cmd == "/status":
                    open_syms = [s for s, p in positions.items() if p] or ["none"]
                    telegram_control.send(f"Equity: {equity:.2f} | Open: {', '.join(open_syms)} | "
                                           f"Paused: {paused} | Kill switch: {risk_engine.kill_switch_tripped}")
                elif cmd == "/brain":
                    telegram_control.send(str(BRAIN.snapshot()))

            today = time.strftime("%Y-%m-%d")
            if today != last_day:
                risk_engine.reset_daily()
                last_day = today

            for symbol in symbols:
                mtf = fetch_multi_timeframe(exchange, symbol, CFG.timeframes)
                technical_signal = generate_signal(mtf)  # context for the AI panel
                price = mtf[CFG.timeframes[-1]]["close"].iloc[-1]
                atr_val = enrich(mtf[CFG.timeframes[-1]])["atr14"].iloc[-1]

                news_score, news_matches = news_feed.news_sentiment(symbol)
                ai_score, ai_notes = multi_ai.get_ai_consensus(symbol, technical_signal,
                                                                headlines=news_matches, exchange=exchange)
                signal = generate_signal(mtf, ai_score=ai_score,
                                          news_score=news_score if news_matches else None)
                note = " | ".join(ai_notes)

                position = positions[symbol]
                if position:
                    hit_stop = (position["action"] == "BUY" and price <= position["stop"]) or \
                               (position["action"] == "SELL" and price >= position["stop"])
                    hit_take = (position["action"] == "BUY" and price >= position["take"]) or \
                               (position["action"] == "SELL" and price <= position["take"])
                    if hit_stop or hit_take:
                        fill = executor.close(exchange, symbol, position["action"], position["size"], price)
                        if fill is not None:
                            direction = 1 if position["action"] == "BUY" else -1
                            pnl = direction * (fill - position["entry"]) * position["size"]
                            equity += pnl
                            risk_engine.update_equity(equity)
                            risk_engine.remove_exposure(CFG.risk_per_trade_pct)
                            db.log_trade(conn, symbol, position["action"], fill, position["size"],
                                         position["stop"], position["take"], mode_label.lower(),
                                         result="tp" if hit_take else "sl", pnl=pnl)
                            db.log_equity(conn, equity)
                            BRAIN.update_from_trade(position["expert_scores"], position["factor_scores_by_tf"],
                                                     position["action"], pnl)
                            logger.info("[%s] Closed %s %s @ %.2f PnL=%+.2f Equity=%.2f brain=%s",
                                        mode_label, symbol, position["action"], fill, pnl, equity,
                                        BRAIN.snapshot()["expert_weights"])
                            positions[symbol] = None
                        # fill is None -> order failed/skipped; position stays tracked, retried next cycle
                        position = positions[symbol]

                decision = risk_engine.evaluate(signal.action, price, atr_val) if not position else None
                approved = bool(decision and decision.approved) and not paused
                db.log_decision(conn, symbol, signal, approved,
                                 (decision.reason if decision else "position already open")
                                 + (" | paused" if paused else ""), note)

                logger.info("[%s] %s %s price=%.2f signal=%s conf=%.0f reasons=%s note=%s",
                            mode_label, symbol, time.strftime("%H:%M:%S"), price,
                            signal.action, signal.confidence, signal.reasons, note)
                if signal.pattern_notes_by_tf:
                    logger.info("[%s] %s chart patterns: %s", mode_label, symbol, signal.pattern_notes_by_tf)

                if approved and not position:
                    fill = executor.open(exchange, symbol, signal.action, decision.position_size, price)
                    if fill is not None:
                        positions[symbol] = {
                            "action": signal.action,
                            "entry": fill,
                            "size": decision.position_size,
                            "stop": decision.stop_loss,
                            "take": decision.take_profit,
                            "expert_scores": signal.expert_scores,
                            "factor_scores_by_tf": signal.factor_scores_by_tf,
                        }
                        risk_engine.add_exposure(CFG.risk_per_trade_pct)
                        db.log_trade(conn, symbol, signal.action, fill, decision.position_size,
                                     decision.stop_loss, decision.take_profit, mode_label.lower(), result="open")
                        logger.info("[%s] OPENED %s %s size=%s stop=%s take=%s",
                                    mode_label, symbol, signal.action, decision.position_size,
                                    decision.stop_loss, decision.take_profit)

            if risk_engine.kill_switch_tripped:
                alert(f"[{mode_label}] KILL SWITCH TRIPPED. Halting new entries until you restart and review.")

            time.sleep(CFG.poll_seconds)
        except KeyboardInterrupt:
            logger.info("[%s] Stopped by user.", mode_label)
            telegram_control.send(f"ZORA {mode_label} trading stopped (Ctrl+C).")
            break
        except Exception as e:
            logger.exception("[%s] error: %s. Retrying in %ds.", mode_label, e, CFG.poll_seconds)
            telegram_control.send(f"[{mode_label}] error: {e}")
            time.sleep(CFG.poll_seconds)


def run_paper() -> None:
    trade_loop(execution.PaperExecutor())


def status() -> None:
    conn = db.connect()
    s = db.summary(conn)
    logger.info("ZORA status")
    logger.info("  Closed trades : %d", s["closed_trades"])
    logger.info("  Realized PnL  : %.2f", s["realized_pnl"])
    logger.info("  Latest equity : %.2f", s["latest_equity"])


def live() -> None:
    if not CFG.live_allowed():
        logger.error("Live trading is not enabled. Set LIVE_TRADING=true, I_UNDERSTAND_THE_RISK=true, "
                      "and EXCHANGE_API_KEY / EXCHANGE_API_SECRET in your .env to unlock this command.")
        return
    print(f"You are about to enable LIVE trading with real funds on {CFG.exchange_id} "
          f"for: {', '.join(CFG.symbols)}")
    print(f"Risk per trade: {CFG.risk_per_trade_pct}% of equity. Max exposure: {CFG.max_exposure_pct}%. "
          f"Max drawdown kill switch: {CFG.max_drawdown_pct}%.")
    print("Stops/take-profits are software-monitored, not exchange-native brackets — if this bot "
          "stops running, open positions have no protection until it's running again.")
    confirm = input("Type EXACTLY 'I ACCEPT THE RISK' to continue: ")
    if confirm.strip() != "I ACCEPT THE RISK":
        logger.info("Confirmation not matched. Aborting — no orders placed.")
        return
    executor = execution.LiveExecutor(telegram_alert=telegram_control.send)
    trade_loop(executor)


def main() -> None:
    configure_logging()
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    override_symbol = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "backtest":
        backtest(symbol=override_symbol, learn=False)
    elif cmd == "learn":
        backtest(symbol=override_symbol, learn=True)
    elif cmd == "brain":
        brain_status()
    elif cmd == "run":
        run_paper()
    elif cmd == "status":
        status()
    elif cmd == "live":
        live()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
