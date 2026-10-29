import pandas as pd
import pytest

from atlas.replay import run_replay
from engine.strategy import load_config
from tests.conftest import ROOT

SEED = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet")


@pytest.mark.parametrize("truncated", [False, True])
def test_replay_matches_simulate_oos(truncated):
    """Ventana de la sección 13: mismos días de operación; equity dentro de 1e-3 (unidades vs pesos)."""
    end = "2025-09-01" if truncated else "2026-10-22"         # truncado es lento: ventana más corta
    r = run_replay(SEED, "2025-03-01", end, load_config(), truncated=truncated).report
    assert r["target_max_abs_diff"] < 1e-9
    assert r["trade_days_equal"], (r["only_replay"], r["only_simulate"])
    assert r["equity_max_rel_diff"] < 1e-3
