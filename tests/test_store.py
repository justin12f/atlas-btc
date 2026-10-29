import pandas as pd

from atlas.data.store import MIGRATIONS, SQLiteStore
from atlas.interfaces import Fill
from tests.conftest import load_fx


def test_migrate_and_wal(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    assert s.schema_version() == len(MIGRATIONS)
    assert s.con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.close()
    assert SQLiteStore(tmp_path / "a.db").schema_version() == len(MIGRATIONS)   # idempotente


def test_candles_upsert_idempotent_and_source_priority(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    rows = load_fx("candles_spot_5m_full")[:10]
    s.upsert_candles("@142", "5m", rows, "hl")
    s.upsert_candles("@142", "5m", rows, "hl")
    assert len(s.candles("@142", "5m")) == 10
    bad = [{**r, "c": "1"} for r in rows[:2]]
    s.upsert_candles("@142", "5m", bad, "binance")           # respaldo no pisa a HL
    assert s.candles("@142", "5m")[0]["c"] == float(rows[0]["c"])
    assert s.last_candle_t("@142", "5m") == rows[-1]["t"]


def test_fills_idempotent(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    f = Fill("f1", "0xabc", "buy", 80000.0, 0.1, 0.00004, "UBTC", "maker", pd.Timestamp("2026-10-29", tz="UTC"))
    assert s.insert_fill("paper", f) is True
    assert s.insert_fill("paper", f) is False
    assert len(s.fills("paper")) == 1


def test_ledger_and_events(tmp_path):
    s = SQLiteStore(tmp_path / "a.db")
    s.write_ledger("paper", "2026-10-28", 5000.0, 0.0625, 80000.0, 10000.0, False, 0.5)
    r = s.ledger_row("paper", "2026-10-28")
    assert r["equity"] == 10000.0 and abs(r["weight"] - 0.5) < 1e-12
    assert s.last_ledger("paper", before="2026-10-29")["date"] == "2026-10-28"
    s.log_event("warn", "test", "hola", {"x": 1})
    assert s.events("test")[0]["message"] == "hola"
