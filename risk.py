"""
risk.py — the risk engine is intentionally separate from strategy.py.
No signal, AI opinion, or confidence score can bypass it. Its job is to
say NO more often than the strategy layer says YES.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import CFG


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    position_size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0


class RiskEngine:
    def __init__(self, equity: float) -> None:
        self.equity = equity
        self.peak_equity = equity
        self.daily_start_equity = equity
        self.open_exposure = 0.0
        self.kill_switch_tripped = False

    def update_equity(self, new_equity: float) -> None:
        self.equity = new_equity
        self.peak_equity = max(self.peak_equity, new_equity)
        drawdown_pct = (self.peak_equity - self.equity) / self.peak_equity * 100
        daily_loss_pct = (self.daily_start_equity - self.equity) / self.daily_start_equity * 100
        if drawdown_pct >= CFG.max_drawdown_pct or daily_loss_pct >= CFG.max_daily_loss_pct:
            self.kill_switch_tripped = True

    def reset_daily(self) -> None:
        self.daily_start_equity = self.equity

    def add_exposure(self, pct: float) -> None:
        self.open_exposure += pct

    def remove_exposure(self, pct: float) -> None:
        self.open_exposure = max(0.0, self.open_exposure - pct)

    def evaluate(self, action: str, price: float, atr_value: float) -> RiskDecision:
        if self.kill_switch_tripped:
            return RiskDecision(False, "kill switch tripped (drawdown/daily loss limit)")

        if action not in ("BUY", "SELL"):
            return RiskDecision(False, "no actionable signal")

        if atr_value <= 0:
            return RiskDecision(False, "invalid volatility reading (ATR<=0)")

        exposure_after = self.open_exposure + CFG.risk_per_trade_pct
        if exposure_after > CFG.max_exposure_pct:
            return RiskDecision(False, "max exposure limit reached")

        risk_amount = self.equity * (CFG.risk_per_trade_pct / 100)
        stop_distance = atr_value * CFG.atr_stop_mult
        if stop_distance <= 0:
            return RiskDecision(False, "invalid stop distance")

        position_size = risk_amount / stop_distance

        if action == "BUY":
            stop_loss = price - stop_distance
            take_profit = price + atr_value * CFG.atr_take_mult
        else:
            stop_loss = price + stop_distance
            take_profit = price - atr_value * CFG.atr_take_mult

        return RiskDecision(
            approved=True,
            reason="ok",
            position_size=round(position_size, 6),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
        )
