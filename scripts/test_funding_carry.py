"""
Prueba pendiente: carry de funding con el USDC ocioso y funding como alarma.
No se pudo correr en el entorno de desarrollo (sin acceso a datos de Binance).
Correlo en tu laptop:

  python scripts/test_funding_carry.py --download     # baja el historial de funding de BTCUSDT
  python scripts/test_funding_carry.py                # corre la prueba con el mismo protocolo

Variantes pre-registradas (no cambiar despues de ver resultados):
  F0: Atlas v3 base (USDC sin rendimiento)
  F1: capital ocioso en cash-and-carry (spot largo + perpetuo corto): cobra el funding diario;
      costo 0.13% por pata cada vez que cambia el capital ocioso
  F2: alarma: exposicion x 0.5 cuando el funding promedio de 7 dias anualizado > 30%
  F3: F1 + F2
Criterio: Reality Check vs F0 con p < 0.05 en 2020-2025 y no peor en el ultimo anio.
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FUND = ROOT / "data" / "raw" / "funding_btcusdt.csv"


def download():
    import requests
    rows = []
    for m in pd.period_range("2019-09", pd.Timestamp.today().strftime("%Y-%m"), freq="M"):
        url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{m}.zip"
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            rows.append(pd.read_csv(z.open(z.namelist()[0])))
    df = pd.concat(rows)
    df.to_csv(FUND, index=False)
    print("guardado", FUND, len(df))


def run():
    from engine.luck_tests import reality_check, bootstrap_sharpes, sharpe_cols, stationary_bootstrap
    from engine.v3 import VariantBuilder, simulate_multi
    f = pd.read_csv(FUND)
    tcol = [c for c in f.columns if "time" in c.lower()][0]
    rcol = [c for c in f.columns if "rate" in c.lower()][0]
    f[tcol] = pd.to_datetime(f[tcol], unit="ms")
    daily = f.set_index(tcol)[rcol].astype(float).resample("D").sum()          # 3 pagos por dia
    ml = pd.read_parquet(ROOT / "results/ml_pred_final.parquet")
    vb = VariantBuilder(ROOT, ml)
    base_v = dict(T="T0", V="ewma", D="none", M="off", C="on", E="off", S="off", P="off")
    tgt = vb.build(base_v)["BTC"]
    fd = daily.reindex(tgt.index).fillna(0.0)
    alarm = (fd.rolling(7).mean() * 365 > 0.30).shift(1).fillna(False)
    out = {}
    for name, use_carry, use_alarm in [("F0", False, False), ("F1", True, False), ("F2", False, True), ("F3", True, True)]:
        t = tgt * np.where(use_alarm & alarm, 0.5, 1.0)
        sim = simulate_multi({"BTC": t}, {"BTC": vb.rets["BTC"]}, gz=False, start="2020-01-01")
        r = sim["ret"].copy()
        if use_carry:
            idle = (1 - sim["weight"]).clip(0, 1)
            r = r + idle * fd.reindex(r.index).fillna(0) - idle.diff().abs().fillna(0) * 2 * 0.0013
        out[name] = r
    R = pd.DataFrame(out).loc["2020-01-01":"2025-10-22"]
    X = R.to_numpy()
    sr = sharpe_cols(X)
    srb = bootstrap_sharpes(X, stationary_bootstrap(len(X), 2000, 20))
    print(pd.Series(sr, index=R.columns).round(3))
    print(reality_check(sr, srb, 0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    download() if a.download else run()
