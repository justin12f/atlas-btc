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
