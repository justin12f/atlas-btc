from types import SimpleNamespace

import pandas as pd

from atlas.dashboard_export import export_dashboard
from atlas.data.store import SQLiteStore


def _store(tmp_path):
    st = SQLiteStore(tmp_path / "a.db")
    for i, (eq_btc, px) in enumerate([(0.05, 100_000.0), (0.05, 102_000.0)]):
        d = f"2026-10-{29 + i}"
        st.write_ledger("paper", d, 5000.0, eq_btc, px, 10_000.0, False, 0.5)
        st.write_ledger("shadow_g1", d, 5000.0, eq_btc, px, 10_000.0, False, 0.5)
    return st


def test_local_sin_remoto(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_DASH_REMOTE", raising=False)
    st = _store(tmp_path)
    assert export_dashboard(st, tmp_path) == "local"
    df = pd.read_csv(tmp_path / "dashboard" / "paper_daily.csv")
    assert list(df["g1"]) == [10_000.0, 10_100.0] and df["btc"].iloc[1] == 10_200.0


def test_sube_con_rclone(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DASH_REMOTE", "gdrive:Atlas/x.csv")
    calls = []
    st = _store(tmp_path)
    ok = lambda cmd, **kw: (calls.append(cmd), SimpleNamespace(returncode=0, stderr=""))[1]  # noqa: E731
    assert export_dashboard(st, tmp_path, run=ok) == "subido"
    assert calls[0][:2] == ["rclone", "copyto"] and calls[0][-1] == "gdrive:Atlas/x.csv"


def test_falla_rclone_alerta_sin_tumbar(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DASH_REMOTE", "gdrive:Atlas/x.csv")
    st = _store(tmp_path)
    sent = []
    alerter = SimpleNamespace(send=lambda m, lvl: sent.append(m))
    bad = lambda cmd, **kw: SimpleNamespace(returncode=1, stderr="token expirado")  # noqa: E731
    assert export_dashboard(st, tmp_path, alerter, run=bad) == "error"
    assert sent and "TABLERO" in sent[0]
    assert any(e["kind"] == "dashboard" for e in st.events(limit=5))
