"""
SQLite en modo WAL (data/live/atlas.db). Esquema de la sección 7.1 del plan.

Migraciones simples: lista ordenada de scripts; `schema_version` guarda el último aplicado.
Cambios al esquema los aprueba el orquestador (contrato entre terminales).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

MIGRATIONS: list[str] = [
    # v1 — esquema inicial (Fase 0)
    """
    CREATE TABLE candles (
        coin TEXT NOT NULL, interval TEXT NOT NULL, t INTEGER NOT NULL,
        o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, v REAL NOT NULL, n INTEGER,
        fetched_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'hl',
        PRIMARY KEY (coin, interval, t));
    CREATE TABLE book_snapshots (
        coin TEXT NOT NULL, ts INTEGER NOT NULL, bids_json TEXT NOT NULL, asks_json TEXT NOT NULL,
        mid REAL, spread_bps REAL, depth_10k_bps REAL, depth_100k_bps REAL,
        PRIMARY KEY (coin, ts));
    CREATE TABLE funding (
        coin TEXT NOT NULL, time INTEGER NOT NULL, funding_rate REAL NOT NULL, premium REAL,
        PRIMARY KEY (coin, time));
    CREATE TABLE daily_bars (
        date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL,
        rv REAL, rv_down REAL, n_5m INTEGER, source TEXT NOT NULL, quality_flags TEXT NOT NULL DEFAULT '');
    CREATE TABLE signals (
        date TEXT NOT NULL, mode TEXT NOT NULL, trend REAL, cycle_mult REAL, vol_fc REAL, vol_scaler REAL,
        target REAL, close REAL, config_hash TEXT NOT NULL, code_version TEXT NOT NULL, computed_at TEXT NOT NULL,
        PRIMARY KEY (date, mode));
    CREATE TABLE orders (
        cloid TEXT PRIMARY KEY, mode TEXT NOT NULL, date TEXT NOT NULL, side TEXT NOT NULL, px REAL, sz REAL NOT NULL,
        tif TEXT, status TEXT NOT NULL, exchange_oid TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE fills (
        mode TEXT NOT NULL, fill_id TEXT NOT NULL, cloid TEXT NOT NULL, side TEXT NOT NULL, px REAL NOT NULL,
        sz REAL NOT NULL, fee REAL NOT NULL, fee_token TEXT NOT NULL, liquidity TEXT NOT NULL, ts TEXT NOT NULL,
        PRIMARY KEY (mode, fill_id));
    CREATE TABLE ledger (
        mode TEXT NOT NULL, date TEXT NOT NULL, usdc REAL NOT NULL, ubtc REAL NOT NULL, mark_px REAL NOT NULL,
        equity REAL NOT NULL, peak REAL NOT NULL, drawdown REAL NOT NULL, paused INTEGER NOT NULL,
        weight REAL NOT NULL, target REAL, PRIMARY KEY (mode, date));
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        status TEXT NOT NULL, error TEXT);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL,
        message TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE shadow (
        date TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
    CREATE TABLE kv (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLiteStore:
    """Implementa atlas.interfaces.Store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    # ------------------------------------------------------------------ esquema
    def schema_version(self) -> int:
        self.con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self.con.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0)

    def migrate(self) -> int:
        v = self.schema_version()
        for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
            with self.tx():
                for stmt in filter(str.strip, sql.split(";")):
                    self.con.execute(stmt)
                self.con.execute("INSERT INTO schema_version VALUES (?)", (i,))
        return self.schema_version()

    def tx(self):
        return _Tx(self.con)

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------ velas
    def upsert_candles(self, coin: str, interval: str, rows: list[dict], source: str = "hl") -> int:
        now = utcnow()
        data = [(coin, interval, int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]),
                 float(r["v"]), int(r.get("n") or 0), now, source) for r in rows]
        with self.tx():
            self.con.executemany(
                """INSERT INTO candles (coin, interval, t, o, h, l, c, v, n, fetched_at, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(coin, interval, t) DO UPDATE SET o=excluded.o, h=excluded.h, l=excluded.l,
                   c=excluded.c, v=excluded.v, n=excluded.n, fetched_at=excluded.fetched_at, source=excluded.source
                   WHERE candles.source = excluded.source OR excluded.source = 'hl'""", data)
        return len(data)

    def candles(self, coin: str, interval: str, start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        q = "SELECT t, o, h, l, c, v, n, source FROM candles WHERE coin=? AND interval=?"
        args: list = [coin, interval]
        if start_ms is not None:
            q += " AND t >= ?"
            args.append(int(start_ms))
        if end_ms is not None:
            q += " AND t <= ?"
            args.append(int(end_ms))
        return [dict(r) for r in self.con.execute(q + " ORDER BY t", args)]

    def candles_df(self, coin: str, interval: str, start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        rows = self.candles(coin, interval, start_ms, end_ms)
        df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v", "n", "source"])
        df.index = pd.to_datetime(df["t"], unit="ms")
        df.index.name = "ts"
        return df

    def last_candle_t(self, coin: str, interval: str) -> int | None:
        r = self.con.execute("SELECT MAX(t) FROM candles WHERE coin=? AND interval=?", (coin, interval)).fetchone()
        return r[0]

    # ------------------------------------------------------------------ libro / funding
    def insert_book(self, coin: str, ts_ms: int, bids: list, asks: list, mid: float, spread_bps: float,
                    depth_10k_bps: float | None, depth_100k_bps: float | None) -> None:
        with self.tx():
            self.con.execute("INSERT OR REPLACE INTO book_snapshots VALUES (?,?,?,?,?,?,?,?)",
                             (coin, int(ts_ms), json.dumps(bids), json.dumps(asks), mid, spread_bps,
                              depth_10k_bps, depth_100k_bps))

    def upsert_funding(self, rows: list[dict]) -> int:
        data = [(r["coin"], int(r["time"]), float(r["fundingRate"]), float(r.get("premium") or 0)) for r in rows]
        with self.tx():
            self.con.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?,?)", data)
        return len(data)

    def last_funding_time(self, coin: str) -> int | None:
        return self.con.execute("SELECT MAX(time) FROM funding WHERE coin=?", (coin,)).fetchone()[0]

    # ------------------------------------------------------------------ barras diarias / señales
    def upsert_daily_bars(self, df: pd.DataFrame) -> int:
        cols = ["open", "high", "low", "close", "volume", "rv", "rv_down", "n_5m", "source", "quality_flags"]
        data = [(str(pd.Timestamp(ix).date()), *[None if pd.isna(r[c]) else (r[c].item() if hasattr(r[c], "item") else r[c])
                                                for c in cols]) for ix, r in df[cols].iterrows()]
        with self.tx():
            self.con.executemany(f"INSERT OR REPLACE INTO daily_bars (date, {', '.join(cols)}) "
                                 f"VALUES ({','.join('?' * (len(cols) + 1))})", data)
        return len(data)

    def daily_bars(self) -> pd.DataFrame:
        df = pd.read_sql("SELECT * FROM daily_bars ORDER BY date", self.con)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")

    def save_signal(self, sig, mode: str) -> None:
        with self.tx():
            self.con.execute("INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (sig.date, mode, sig.trend, sig.cycle_mult, sig.vol_fc, sig.vol_scaler, sig.target,
                              sig.close, sig.config_hash, sig.code_version, utcnow()))

    def signal(self, date: str, mode: str) -> dict | None:
        r = self.con.execute("SELECT * FROM signals WHERE date=? AND mode=?", (date, mode)).fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------ órdenes / fills / ledger
    def upsert_order(self, cloid: str, mode: str, date: str, side: str, px: float | None, sz: float, tif: str | None,
                     status: str, exchange_oid: str | None = None, meta: dict | None = None) -> None:
        now = utcnow()
        with self.tx():
            self.con.execute(
                """INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cloid) DO UPDATE SET px=excluded.px, sz=excluded.sz, tif=excluded.tif,
                   status=excluded.status, exchange_oid=COALESCE(excluded.exchange_oid, orders.exchange_oid),
                   updated_at=excluded.updated_at, meta_json=excluded.meta_json""",
                (cloid, mode, date, side, px, sz, tif, status, exchange_oid, now, now, json.dumps(meta or {})))

    def orders(self, mode: str, date: str | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM orders WHERE mode=?", [mode]
        if date:
            q += " AND date=?"
            args.append(date)
        if status:
            q += " AND status=?"
            args.append(status)
        return [dict(r) for r in self.con.execute(q + " ORDER BY created_at", args)]

    def insert_fill(self, mode: str, f) -> bool:
        """Devuelve False si el fill ya existía (idempotente)."""
        with self.tx():
            cur = self.con.execute("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (mode, f.fill_id, f.cloid, f.side, f.px, f.sz, f.fee, f.fee_token, f.liquidity,
                                    f.ts.isoformat() if hasattr(f.ts, "isoformat") else str(f.ts)))
        return cur.rowcount == 1

    def fills(self, mode: str, cloid: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM fills WHERE mode=?", [mode]
        if cloid:
            q += " AND cloid=?"
            args.append(cloid)
        return [dict(r) for r in self.con.execute(q + " ORDER BY ts", args)]

    def write_ledger(self, mode: str, date: str, usdc: float, ubtc: float, mark_px: float, peak: float,
                     paused: bool, target: float | None) -> None:
        eq = usdc + ubtc * mark_px
        with self.tx():
            self.con.execute("INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (mode, date, usdc, ubtc, mark_px, eq, peak, eq / peak - 1 if peak > 0 else 0.0,
                              int(paused), ubtc * mark_px / eq if eq > 0 else 0.0, target))

    def ledger_row(self, mode: str, date: str) -> dict | None:
        r = self.con.execute("SELECT * FROM ledger WHERE mode=? AND date=?", (mode, date)).fetchone()
        return dict(r) if r else None

    def last_ledger(self, mode: str, before: str | None = None) -> dict | None:
        q, args = "SELECT * FROM ledger WHERE mode=?", [mode]
        if before:
            q += " AND date < ?"
            args.append(before)
        r = self.con.execute(q + " ORDER BY date DESC LIMIT 1", args).fetchone()
        return dict(r) if r else None

    def ledger_df(self, mode: str) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM ledger WHERE mode=? ORDER BY date", self.con, params=(mode,))

    # ------------------------------------------------------------------ runs / eventos / kv
    def start_run(self, run_id: str, job: str) -> None:
        with self.tx():
            self.con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (run_id, job, utcnow(), None, "running", None))

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.tx():
            self.con.execute("UPDATE runs SET finished_at=?, status=?, error=? WHERE run_id=?",
                             (utcnow(), status, error, run_id))

    def log_event(self, level: str, kind: str, message: str, payload: dict | None = None) -> None:
        with self.tx():
            self.con.execute("INSERT INTO events (ts, level, kind, message, payload_json) VALUES (?,?,?,?,?)",
                             (utcnow(), level, kind, message, json.dumps(payload or {}, default=str)))

    def events(self, kind: str | None = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM events", []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        return [dict(r) for r in self.con.execute(q + " ORDER BY id DESC LIMIT ?", [*args, limit])]

    def kv_get(self, key: str) -> str | None:
        r = self.con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def kv_set(self, key: str, value: str) -> None:
        with self.tx():
            self.con.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, value, utcnow()))


class _Tx:
    """BEGIN IMMEDIATE / COMMIT / ROLLBACK (la conexión está en autocommit)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self):
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
            self.own = True
        else:
            self.own = False
        return self.con

    def __exit__(self, exc_type, *_):
        if self.own:
            self.con.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
