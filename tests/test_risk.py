from atlas.risk import RiskInputs, evaluate


def base(**kw):
    return RiskInputs(**{"equity": 10_000, "peak": 10_000, "paused": False, "kill_dd": 0.36, "data_age_min": 5,
                         **kw})


def test_all_good():
    d = evaluate(base())
    assert d.trade_allowed and not d.kill and d.blocks == []


def test_stale_and_flags_and_divergence_block():
    d = evaluate(base(data_age_min=45, quality_flags="n_5m=280", divergences={"perp": 0.02},
                      market_changed=True))
    assert not d.trade_allowed and len(d.blocks) == 4


def test_rv_binance_flag_does_not_block():
    assert evaluate(base(quality_flags="rv_binance")).trade_allowed


def test_kill_switch_even_with_bad_data():
    d = evaluate(base(equity=6_000, data_age_min=None))
    assert d.kill and not d.trade_allowed and any("INTERRUPTOR" in a for a in d.alerts)


def test_no_kill_when_paused():
    assert not evaluate(base(equity=6_000, paused=True)).kill


def test_drawdown_alerts_once_per_level():
    d = evaluate(base(equity=8_400))
    assert d.alerts and d.new_alert_level == 0.15
    d2 = evaluate(base(equity=8_300, last_alerted_level=0.15))
    assert not d2.alerts
    d3 = evaluate(base(equity=9_900, last_alerted_level=0.15))
    assert d3.new_alert_level == 0.0
