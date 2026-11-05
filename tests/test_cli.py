import json

import pytest

from atlas import cli


@pytest.fixture(autouse=True)
def tmp_data(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))


def test_status_empty_db(capsys):
    assert cli.main(["status"]) == 0
    assert "schema_version" in json.loads(capsys.readouterr().out)


def test_resume_requires_note(capsys):
    assert cli.main(["resume", "--mode", "paper", "--note", "corto"]) == 2


def test_daily_testnet_disabled(capsys):
    assert cli.main(["daily", "--mode", "testnet"]) == 0
    assert "desactivado" in capsys.readouterr().out


def test_resume_resets_peak(capsys):
    st = cli._store(cli.load_live_config())
    st.write_ledger("paper", "2026-10-28", 6_000.0, 0.0, 70_000.0, 10_000.0, True, 0.0)
    assert cli.main(["resume", "--mode", "paper", "--note", "revisé datos y mercado, todo normal"]) == 0
    r = st.last_ledger("paper")
    assert not r["paused"] and r["peak"] == pytest.approx(6_000)


def test_backfill_from_fixtures(capsys):
    st = cli._store(cli.load_live_config())
    st.kv_set("market_cache:UBTC/USDC", json.dumps({"coin": "@142"}))
    assert cli.main(["backfill", "--from-fixtures"]) == 0
    assert st.last_candle_t("@142", "5m") is not None


class _Rec:
    def __init__(self):
        self.sent = []

    def send(self, text, level="info"):
        self.sent.append((level, text))


def test_collect_fail_streak_alerts_once_and_on_recovery():
    st = cli._store(cli.load_live_config())
    a = _Rec()
    assert cli.note_collect_result(st, a, ["ConnectionError: refused"]) == 1 and a.sent == []
    assert cli.note_collect_result(st, a, ["ConnectionError: refused"]) == 2 and a.sent[0][0] == "error"
    assert cli.note_collect_result(st, a, ["ConnectionError: refused"]) == 3 and len(a.sent) == 1
    assert cli.note_collect_result(st, a, []) == 0 and "recuperado" in a.sent[1][1]
    assert cli.note_collect_result(st, a, []) == 0 and len(a.sent) == 2


def test_collect_without_network_logs_and_returns_1(monkeypatch, capsys):
    def boom(cfg):
        raise ConnectionError("sin red")
    monkeypatch.setattr(cli, "_info", boom)
    assert cli.main(["collect"]) == 1
    assert "sin red" in capsys.readouterr().out
    st = cli._store(cli.load_live_config())
    ev = st.con.execute("SELECT level, kind FROM events WHERE kind='collect'").fetchall()
    assert [tuple(r) for r in ev] == [("error", "collect")]
