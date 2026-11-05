"""
Paridad de datos (sección 7.3 / criterio de F1 y de 15.2): Hyperliquid spot vs referencias.

Por día UTC completo: cierre (última vela de 5m, la de las 23:55) y rv (suma de retornos log de 5m al
cuadrado, misma definición que engine/data.py). Referencias: Binance BTCUSDC 5m (source='binance') y
Bitstamp (semilla del backtest, hasta 2026-10-22). Umbrales del pre-registro:
  mediana |cierre HL / ref − 1| ≤ 0.10%   ·   mediana rv HL / rv ref en [0.85, 1.15]
"""
from __future__ import annotations

import pandas as pd

from atlas.data.stitch import rv_by_day

CLOSE_MAX = 0.0010
RV_RANGE = (0.85, 1.15)


def daily_close(c5: pd.DataFrame) -> pd.Series:
    """Cierre del día = cierre de la vela de 5m de las 23:55 (si falta, el día no cuenta)."""
    last = c5[c5.index.time == pd.Timestamp("23:55").time()]["c"]
    last.index = last.index.floor("D")
    return last


def parity_table(hl5: pd.DataFrame, refs: dict[str, pd.DataFrame], seed: pd.DataFrame | None = None,
                 start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Una fila por día con cierre y rv de HL y de cada referencia, y sus diferencias."""
    out = pd.DataFrame({"close_hl": daily_close(hl5), "rv_hl": rv_by_day(hl5)["rv"]})
    for name, c5 in refs.items():
        out[f"close_{name}"] = daily_close(c5)
        out[f"rv_{name}"] = rv_by_day(c5)["rv"]
    if seed is not None:
        s = seed[["close", "rv"]].copy()
        s.index = pd.DatetimeIndex(s.index).astype("datetime64[ns]")
        out["close_bitstamp"] = s["close"].reindex(out.index)
        out["rv_bitstamp"] = s["rv"].reindex(out.index)
    for name in [*refs, *(["bitstamp"] if seed is not None else [])]:
        out[f"close_diff_{name}"] = out["close_hl"] / out[f"close_{name}"] - 1
        out[f"rv_ratio_{name}"] = out["rv_hl"] / out[f"rv_{name}"]
    out = out.loc[start:end] if (start or end) else out
    return out.dropna(subset=["close_hl"])


def parity_summary(t: pd.DataFrame) -> dict:
    res = {"days": int(len(t)), "from": str(t.index.min().date()) if len(t) else None,
           "to": str(t.index.max().date()) if len(t) else None, "refs": {}}
    for col in [c for c in t.columns if c.startswith("close_diff_")]:
        name = col.removeprefix("close_diff_")
        cd = t[col].abs().dropna()
        rr = t[f"rv_ratio_{name}"].dropna()
        med_c = float(cd.median()) if len(cd) else None
        med_r = float(rr.median()) if len(rr) else None
        res["refs"][name] = {
            "days_close": int(len(cd)), "close_absdiff_median": med_c,
            "close_absdiff_max": float(cd.max()) if len(cd) else None,
            "days_rv": int(len(rr)), "rv_ratio_median": med_r,
            "rv_ratio_min": float(rr.min()) if len(rr) else None, "rv_ratio_max": float(rr.max()) if len(rr) else None,
            "pass_close": med_c is not None and med_c <= CLOSE_MAX,
            "pass_rv": med_r is not None and RV_RANGE[0] <= med_r <= RV_RANGE[1],
        }
    return res


def parity_markdown(t: pd.DataFrame, s: dict) -> str:
    lines = [f"# Paridad de datos HL spot vs referencias — {s['from']} → {s['to']} ({s['days']} días)", "",
             "| Referencia | Días | Mediana |Δ cierre| | Máx |Δ cierre| | Mediana rv HL/ref | Rango rv | Cierre ≤ 0.10% | rv en [0.85, 1.15] |",
             "|---|---|---|---|---|---|---|---|"]
    for name, r in s["refs"].items():
        f = lambda x, pct=True: "—" if x is None else (f"{x:.4%}" if pct else f"{x:.3f}")  # noqa: E731
        lines.append(f"| {name} | {r['days_close']} | {f(r['close_absdiff_median'])} | {f(r['close_absdiff_max'])} | "
                     f"{f(r['rv_ratio_median'], False)} | {f(r['rv_ratio_min'], False)}–{f(r['rv_ratio_max'], False)} | "
                     f"{'✅' if r['pass_close'] else '❌'} | {'✅' if r['pass_rv'] else '❌'} |")
    cols = [c for c in t.columns if c.startswith(("close_diff_", "rv_ratio_"))]
    lines += ["", "## Por día", "", "| día | cierre HL | " + " | ".join(cols) + " |", "|---" * (len(cols) + 2) + "|"]
    for d, row in t.iterrows():
        vals = " | ".join("—" if pd.isna(row[c]) else f"{row[c]:.5f}" for c in cols)
        lines.append(f"| {d.date()} | {row['close_hl']:,.1f} | {vals} |")
    return "\n".join(lines) + "\n"
