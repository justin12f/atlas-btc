"""La regla de banda extraída (engine.portfolio.should_trade) es exactamente la de simulate."""
import itertools

import numpy as np

from engine.portfolio import should_trade


def _inline(e, w, band):
    return abs(e - w) >= band or (e < 0.02 and w >= 0.02) or (e > 0.98 and w <= 0.98 and abs(e - w) > 0.02)


def _legacy_paper_step(e, w, band):      # engine/live.py: sin la tercera condición
    return abs(e - w) >= band or (e < 0.02 and w >= 0.02)


def test_matches_inline_rule():
    grid = np.round(np.linspace(0, 1, 101), 4)
    for e, w in itertools.product(grid, grid):
        assert should_trade(e, w, 0.10) == _inline(e, w, 0.10)


def test_legacy_live_misses_full_entry():
    # caso documentado en el plan §9.1: entrada a 100% con diferencia < banda
    assert should_trade(1.0, 0.95, 0.10) and not _legacy_paper_step(1.0, 0.95, 0.10)
