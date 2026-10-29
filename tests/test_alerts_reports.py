
from atlas.alerts import Alerter
from atlas.data.store import SQLiteStore
from atlas.reports import daily_report, weekly_report, write_report


class Resp:
    status_code = 200


def test_alerter_noop_without_env(monkeypatch, tmp_path):
    for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "HEALTHCHECKS_URL"):
        monkeypatch.delenv(v, raising=False)
    st = SQLiteStore(tmp_path / "a.db")
    calls = []
    a = Alerter(st, post=lambda *a, **k: calls.append(a))
    assert a.send("hola") is False and a.ping() is False and calls == []
    assert st.events(kind="alert")[0]["message"] == "hola"


def test_alerter_sends(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("HEALTHCHECKS_URL", "https://hc.example/abc")
    calls = []
    a = Alerter(post=lambda url, **k: calls.append(url) or Resp())
    assert a.send("hola") and a.ping(False)
    assert calls[1] == "https://hc.example/abc/fail"


def test_reports(tmp_path):
    st = SQLiteStore(tmp_path / "a.db")
    for i, d in enumerate(["2026-10-26", "2026-10-27", "2026-10-28"]):
        st.write_ledger("paper", d, 10_000 - i, 0.0, 70_000.0, 10_000, False, 0.0)
    st.start_run("r1", "daily:paper")
    st.finish_run("r1", "done")
    txt = daily_report(st, "paper", "2026-10-28")
    assert "Equity" in txt and "Sin señal" in txt
    wk, data = weekly_report(st, "2026-10-28")
    assert "Días con ledger: 3/7" in wk and len(data) == 3
    p = write_report(tmp_path / "rep", "w", wk, data)
    assert p.exists() and (tmp_path / "rep" / "w.csv").exists()
