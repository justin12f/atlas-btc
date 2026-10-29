"""
Riesgo y salvaguardas (sección 10).

- Interruptor de caída: equity / máximo − 1 < −kill_switch_dd (de config/strategy.yaml) -> objetivo 0 y pausa.
  Es la ÚNICA acción que se ejecuta aunque haya datos dudosos (regla 6).
- Modo seguro (no operar, mantener posición): datos viejos, barras con banderas, divergencia vs referencia,
  spotMeta cambió, error de señal o de reconciliación.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskInputs:
    equity: float
    peak: float
    paused: bool
    kill_dd: float
    data_age_min: float | None = None
    max_data_age_min: float = 20
    quality_flags: str = ""
    divergences: dict = field(default_factory=dict)   # nombre -> |px/ref - 1|
    max_divergence: float = 0.01
    market_changed: bool = False
    extra_blocks: list = field(default_factory=list)  # errores de señal / reconciliación
    drawdown_levels: tuple = (0.15, 0.25, 0.36)
    last_alerted_level: float = 0.0


@dataclass
class RiskDecision:
    trade_allowed: bool
    kill: bool
    drawdown: float
    blocks: list[str]
    alerts: list[str]
    new_alert_level: float


def evaluate(x: RiskInputs) -> RiskDecision:
    peak = max(x.peak, x.equity)
    dd = x.equity / peak - 1 if peak > 0 else 0.0
    blocks: list[str] = []
    alerts: list[str] = []
    if x.data_age_min is None or x.data_age_min > x.max_data_age_min:
        blocks.append(f"datos viejos ({x.data_age_min} min > {x.max_data_age_min})")
    flags = [f for f in (x.quality_flags or "").split(",") if f and f != "rv_binance"]
    if flags:
        blocks.append(f"barra con banderas: {','.join(flags)}")
    for name, div in x.divergences.items():
        if div is None or div > x.max_divergence:
            blocks.append(f"divergencia {name}={div}")
    if x.market_changed:
        blocks.append("spotMeta cambió (índice o szDecimals)")
    blocks += list(x.extra_blocks)
    kill = (not x.paused) and dd < -x.kill_dd
    if kill:
        alerts.append(f"INTERRUPTOR: caída {dd:.2%} < -{x.kill_dd:.0%} -> todo a USDC y pausa")
    level = x.last_alerted_level
    for lv in sorted(x.drawdown_levels):
        if dd <= -lv and lv > level:
            alerts.append(f"caída {dd:.2%} cruzó -{lv:.0%}")
            level = lv
    if dd > -min(x.drawdown_levels):
        level = 0.0                               # se rearma al recuperarse
    return RiskDecision(trade_allowed=not blocks, kill=kill, drawdown=dd, blocks=blocks, alerts=alerts,
                        new_alert_level=level)
