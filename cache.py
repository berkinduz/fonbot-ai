from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd


class SQLiteCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS fund_prices (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                price REAL NOT NULL,
                source TEXT DEFAULT 'unknown',
                fetched_at TEXT,
                PRIMARY KEY(code, date)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS metadata (
                code TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                aum REAL,
                stock_ratio REAL,
                updated_at TEXT
                )"""
            )
            self._ensure_column(con, "fund_prices", "source", "TEXT DEFAULT 'unknown'")
            self._ensure_column(con, "fund_prices", "fetched_at", "TEXT")

    def _ensure_column(self, con, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def save_prices(self, df: pd.DataFrame, source: str = "unknown", fetched_at: Optional[str] = None) -> None:
        if df.empty:
            return
        rows = df[["code", "date", "price"]].copy()
        rows["date"] = pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d")
        rows["source"] = source
        rows["fetched_at"] = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as con:
            rows.to_sql("_tmp_prices", con, if_exists="replace", index=False)
            con.execute(
                """INSERT OR REPLACE INTO fund_prices(code, date, price, source, fetched_at)
                SELECT code, date, price, source, fetched_at FROM _tmp_prices"""
            )
            con.execute("DROP TABLE _tmp_prices")

    def load_prices(self, code: str) -> pd.DataFrame:
        with self._connect() as con:
            df = pd.read_sql_query(
                "SELECT code, date, price FROM fund_prices WHERE code=? ORDER BY date", con, params=(code,)
            )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def price_cache_age(self, code: str) -> Optional[dict]:
        with self._connect() as con:
            row = con.execute(
                "SELECT MAX(date), COALESCE(MAX(fetched_at), ''), COALESCE(MAX(source), 'cache') FROM fund_prices WHERE code=?",
                (code,),
            ).fetchone()
        if not row or not row[0]:
            return None
        latest_date = pd.to_datetime(row[0]).date()
        age_days = (datetime.now(timezone.utc).date() - latest_date).days
        return {"code": code, "latest_date": str(latest_date), "age_days": age_days, "source": row[2] or "cache"}
