import json

import numpy as np
import pandas as pd

from atlas.data.store import SQLiteStore
from atlas.shadow import record_shadow


def supply(falling: bool) -> pd.Series:
    idx = pd.date_range("2026-08-01", "2026-10-28", freq="D")
    v = np.linspace(2e11, 1.8e11 if falling else 2.2e11, len(idx))
    return pd.Series(v, index=idx)


def test_shadow_halves_when_supply_falls(tmp_path):
    st = SQLiteStore(tmp_path / "a.db")
    r = record_shadow(st, "2026-10-28", 0.75, fetch=lambda: supply(True))
    assert r["shadow_mult"] == 0.5 and r["shadow_target"] == 0.375
    row = st.con.execute("SELECT payload_json FROM shadow WHERE date='2026-10-28'").fetchone()
    assert json.loads(row[0])["atlas_target"] == 0.75
    assert record_shadow(st, "2026-10-28", 0.75, fetch=lambda: supply(False))["shadow_mult"] == 1.0


def test_shadow_failure_is_event(tmp_path):
    st = SQLiteStore(tmp_path / "a.db")
    def boom():
        raise ConnectionError("sin red")
    assert record_shadow(st, "2026-10-28", 0.75, fetch=boom) is None
    assert st.events(kind="shadow")
