"""
telegram_control.py — optional Telegram alerts + remote control.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable both. Leave
either blank and this whole module quietly no-ops — the bot runs exactly
as before, just without alerts or remote commands.

SECURITY: only messages from the exact configured TELEGRAM_CHAT_ID are ever
acted on. Anyone else messaging your bot (e.g. if they find its username)
is silently ignored — they cannot pause, resume, or kill your bot, and
cannot see your alerts.

How to set it up:
  1. Message @BotFather on Telegram, /newbot, follow the prompts — you get
     a token that looks like 123456789:AA...
  2. Message your new bot anything once (so it can see your chat).
  3. Visit https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates in a browser
     and read off your numeric "chat":{"id": ...} — that's TELEGRAM_CHAT_ID.

Commands (send as a normal message to your bot):
  /status   current equity, open positions, kill-switch state
  /pause    stop opening new positions (existing ones still managed normally)
  /resume   allow new positions again
  /kill     manually trip the kill switch — same effect as hitting a
            drawdown/daily-loss limit, and works even if you're away from
            the phone/Termux session itself
  /brain    show the auto-learning brain's current weights
"""
from __future__ import annotations

import requests

from config import CFG


def enabled() -> bool:
    return bool(CFG.telegram_bot_token and CFG.telegram_chat_id)


def send(text: str) -> None:
    """Best-effort alert — a Telegram hiccup should never crash the trading loop."""
    if not enabled():
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{CFG.telegram_bot_token}/sendMessage",
            json={"chat_id": CFG.telegram_chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


class CommandPoller:
    """Non-blocking poll for new commands since the last check (long-poll timeout=0)."""

    def __init__(self) -> None:
        self.offset: int | None = None

    def poll(self) -> list[str]:
        """Return a list of command strings (e.g. '/pause') from the configured chat only."""
        if not enabled():
            return []
        try:
            params = {"timeout": 0}
            if self.offset is not None:
                params["offset"] = self.offset
            r = requests.get(
                f"https://api.telegram.org/bot{CFG.telegram_bot_token}/getUpdates",
                params=params, timeout=10,
            )
            r.raise_for_status()
            updates = r.json().get("result", [])
        except Exception:
            return []

        commands = []
        for u in updates:
            self.offset = u["update_id"] + 1
            msg = u.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if chat_id == str(CFG.telegram_chat_id) and text.startswith("/"):
                commands.append(text.lower().split()[0])
        return commands
