import pytest

from atlas.hl.client import HLError, HLInfo, WeightBudget, parse_book, request_weight
from tests.conftest import load_fx


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, payload, timeout):
        self.calls.append(payload)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_weights():
    assert request_weight({"type": "l2Book"}) == 2
    assert request_weight({"type": "candleSnapshot"}) == 20


def test_retry_on_429_then_ok():
    sleeps = []
    t = FakeTransport([(429, "rate"), (503, "x"), (200, {"ok": 1})])
    c = HLInfo("https://x", transport=t, sleep=sleeps.append)
    assert c.post({"type": "allMids"}) == {"ok": 1}
    assert len(sleeps) == 2 and sleeps[0] >= 2.0


def test_retry_on_timeout_and_give_up():
    t = FakeTransport([TimeoutError("t")] * 3)
    c = HLInfo("https://x", transport=t, max_retries=2, sleep=lambda s: None)
    with pytest.raises(HLError, match="reintentos"):
        c.post({"type": "allMids"})


def test_client_error_not_retried():
    t = FakeTransport([(422, "bad")])
    with pytest.raises(HLError, match="422"):
        HLInfo("https://x", transport=t, sleep=lambda s: None).post({"type": "meta"})
    assert len(t.calls) == 1


def test_budget_blocks_when_exhausted():
    clock = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        clock[0] += s
    b = WeightBudget(40, now=lambda: clock[0], sleep=sleep)
    b.acquire(20)
    b.acquire(20)
    b.acquire(20)              # debe esperar a que salga la primera de la ventana
    assert slept and clock[0] >= 60


def test_candles_charge_extra_weight():
    rows = load_fx("candles_spot_5m_full")
    c = HLInfo("https://x", transport=FakeTransport([(200, rows)]), sleep=lambda s: None)
    out = c.candles("@142", "5m", 0, 1)
    assert len(out) == len(rows)
    assert c.budget.used() == 20 + len(rows) // 60


def test_parse_book_fixture():
    b = parse_book(load_fx("l2book_spot_ubtc"))
    assert b.best_bid < b.best_ask and len(b.bids) == 20
    assert b.ts.tzinfo is not None
