"""
Paper trading diario de Atlas v3 (sin dinero real) + registro de senales en sombra.

  python scripts/paper_trade.py                     # descarga BTCUSDC de Binance y ejecuta un paso
  python scripts/paper_trade.py --offline           # prueba con los datos locales de Bitstamp
  python scripts/paper_trade.py --resume "motivo"   # reanuda despues de una pausa del interruptor
  python scripts/paper_trade.py --report            # resumen semanal

Cron sugerido en tu laptop o VPS (00:10 UTC):
  10 0 * * * cd ~/btc_quant_v2 && python3 scripts/paper_trade.py >> data/live/cron.log 2>&1
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.live import LIVE, daily_from_5m, fetch_klines, paper_step, resume  # noqa: E402
from engine.shadow import fetch_supply_llama, log_today, supply_from_coinmetrics  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--offline", action="store_true")
ap.add_argument("--resume", type=str, default=None)
ap.add_argument("--report", action="store_true")
args = ap.parse_args()

if args.resume:
    resume(args.resume)
    sys.exit(0)

if args.report:
    log = pd.read_csv(LIVE / "paper_log.csv", parse_dates=["date"]).set_index("date")
    eq = log["equity"]
    last7 = eq.iloc[-1] / eq.iloc[max(0, len(eq) - 8)] - 1
    print(f"Capital: {eq.iloc[-1]:,.2f} USDC | 7 dias: {last7:+.2%} | desde inicio: {eq.iloc[-1] / eq.iloc[0] - 1:+.2%}")
    print(f"Caida actual: {log['drawdown'].iloc[-1]:.1%} | exposicion objetivo: {log['target'].iloc[-1]:.0%} | pausa: {log['paused'].iloc[-1]}")
    sys.exit(0)

if args.offline:
    d = pd.read_parquet(ROOT / "data/processed/btc_daily.parquet").iloc[-450:]
    supply = supply_from_coinmetrics(pd.read_parquet(ROOT / "data/processed/onchain.parquet"))
else:
    d = daily_from_5m(fetch_klines("BTCUSDC", "5m", days=420))
    try:
        supply = fetch_supply_llama()
    except Exception as exc:                     # la sombra nunca debe tumbar el paso de trading
        print("sombra de stablecoins sin datos hoy:", exc)
        supply = None

row = paper_step(d)
print(json.dumps(row, indent=2, default=str))
if supply is not None:
    print("sombra:", json.dumps(log_today(supply, row["target"], row["date"]), default=str))
