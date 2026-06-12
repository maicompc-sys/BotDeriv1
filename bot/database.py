import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.db")

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT,
        symbol TEXT,
        strategy TEXT,
        contract_type TEXT,
        granularity TEXT,
        stake REAL,
        payout REAL,
        profit REAL,
        result TEXT,
        entry_spot REAL,
        exit_spot REAL,
        entry_time TEXT,
        exit_time TEXT,
        duration INTEGER,
        tick_count INTEGER,
        status TEXT DEFAULT 'open',
        notes TEXT
    );
    CREATE TABLE IF NOT EXISTS strategy_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT UNIQUE,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        total_profit REAL DEFAULT 0,
        max_drawdown REAL DEFAULT 0,
        win_rate REAL DEFAULT 0,
        avg_profit REAL DEFAULT 0,
        last_updated TEXT
    );
    CREATE TABLE IF NOT EXISTS tick_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        price REAL,
        timestamp TEXT,
        epoch INTEGER
    );
    CREATE TABLE IF NOT EXISTS equity_curve (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        balance REAL,
        equity REAL,
        open_pnl REAL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    INSERT OR IGNORE INTO settings VALUES ('app_token', '');
    INSERT OR IGNORE INTO settings VALUES ('default_stake', '1.00');
    INSERT OR IGNORE INTO settings VALUES ('max_stake', '100.00');
    INSERT OR IGNORE INTO settings VALUES ('daily_loss_limit', '50.00');
    INSERT OR IGNORE INTO settings VALUES ('martingale_multiplier', '2.1');
    INSERT OR IGNORE INTO settings VALUES ('max_martingale_steps', '4');
    INSERT OR IGNORE INTO settings VALUES ('kelly_fraction', '0.25');
    INSERT OR IGNORE INTO settings VALUES ('risk_per_trade', '2.0');
    """)
    conn.commit()
    conn.close()

def get_setting(key, default=""):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()

def insert_trade(data: dict):
    conn = get_conn()
    cols = ",".join(data.keys())
    phs  = ",".join(["?"] * len(data))
    conn.execute(f"INSERT INTO trades ({cols}) VALUES ({phs})", list(data.values()))
    conn.commit()
    conn.close()

def update_trade(contract_id, data: dict):
    conn = get_conn()
    sets = ",".join([f"{k}=?" for k in data.keys()])
    conn.execute(f"UPDATE trades SET {sets} WHERE contract_id=?",
                 list(data.values()) + [contract_id])
    conn.commit()
    conn.close()

def get_trades(limit=200, symbol=None, strategy=None):
    conn   = get_conn()
    q      = "SELECT * FROM trades"
    params = []
    conds  = []
    if symbol:   conds.append("symbol=?");   params.append(symbol)
    if strategy: conds.append("strategy=?"); params.append(strategy)
    if conds: q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_strategy_perf(strategy, win: bool, profit: float):
    conn = get_conn()
    row  = conn.execute("SELECT * FROM strategy_performance WHERE strategy=?",
                        (strategy,)).fetchone()
    now  = datetime.now().isoformat()
    if not row:
        wins   = 1 if win else 0
        losses = 0 if win else 1
        conn.execute(
            "INSERT INTO strategy_performance VALUES (NULL,?,1,?,?,?,0,?,?,?)",
            (strategy, wins, losses, profit, wins, profit, now))
    else:
        total        = row["total_trades"] + 1
        wins         = row["wins"]  + (1 if win else 0)
        losses       = row["losses"] + (0 if win else 1)
        total_profit = row["total_profit"] + profit
        win_rate     = wins / total
        avg_profit   = total_profit / total
        conn.execute("""UPDATE strategy_performance
            SET total_trades=?,wins=?,losses=?,total_profit=?,
                win_rate=?,avg_profit=?,last_updated=?
            WHERE strategy=?""",
            (total, wins, losses, total_profit, win_rate, avg_profit, now, strategy))
    conn.commit()
    conn.close()

def get_strategy_stats():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM strategy_performance ORDER BY win_rate DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def insert_equity(balance, equity, open_pnl=0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO equity_curve(timestamp,balance,equity,open_pnl) VALUES(?,?,?,?)",
        (datetime.now().isoformat(), balance, equity, open_pnl))
    conn.commit()
    conn.close()

def get_equity_curve(limit=500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM equity_curve ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]