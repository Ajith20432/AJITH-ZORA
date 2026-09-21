"""
db.py — SQLite storage for trades, equity snapshots, and the decision log.
"""
from __future__ import annotations

import sqlite3
import time

from config import CFG


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CFG.db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            symbol TEXT,
            action TEXT,
            price REAL,
            size REAL,
            stop_loss REAL,
            take_profit REAL,
            mode TEXT,
            result TEXT,
            pnl REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_curve (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            equity REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            symbol TEXT,
            action TEXT,
            confidence REAL,
            approved INTEGER,
            reason TEXT,
            llm_note TEXT
        )
    """)
    conn.commit()
    return conn


def log_decision(
    conn: sqlite3.Connection, symbol: str, signal, approved: bool, reason: str, llm_note: str = ""
) -> None:
    conn.execute(
        "INSERT INTO decision_log (ts, symbol, action, confidence, approved, reason, llm_note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), symbol, signal.action, signal.confidence, int(approved), reason, llm_note),
    )
    conn.commit()


def log_trade(
    conn: sqlite3.Connection, symbol: str, action: str, price: float, size: float,
    stop_loss: float, take_profit: float, mode: str, result: str = "open", pnl: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO trades (ts, symbol, action, price, size, stop_loss, take_profit, mode, result, pnl) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), symbol, action, price, size, stop_loss, take_profit, mode, result, pnl),
    )
    conn.commit()


def log_equity(conn: sqlite3.Connection, equity: float) -> None:
    conn.execute("INSERT INTO equity_curve (ts, equity) VALUES (?, ?)", (time.time(), equity))
    conn.commit()


def summary(conn: sqlite3.Connection) -> dict:
    cur = conn.execute("SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM trades WHERE result != 'open'")
    count, pnl = cur.fetchone()
    cur = conn.execute("SELECT equity FROM equity_curve ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    latest_equity = row[0] if row else CFG.starting_capital
    return {"closed_trades": count, "realized_pnl": pnl, "latest_equity": latest_equity}
