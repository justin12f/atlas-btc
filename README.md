# Atlas BTC/USDC v3 — engine cuantitativo

Estrategia spot (sin apalancamiento ni cortos) que decide cada día qué porcentaje del capital tener
en BTC y deja el resto en USDC. Backtest walk-forward 2018-01 → 2026-10 con holdout limpio de 12 meses.

| Ene 2018 – oct 2026 | Rend. anual | Sharpe | Caída máx. | Últimos 12 meses |
|---|---|---|---|---|
| Atlas v3 | 30.9% | 1.20 | −23.9% | +3.4% |
| Atlas v2 (con ML) | 29.0% | 1.15 | −23.9% | +3.4% |
| BTC buy & hold | 22.7% | 0.64 | −81.5% | −24.4% |
| QQQ | 19.9% | 0.88 | −35.1% | +20.3% |
| Magnificent 7 | 33.9% | 1.12 | −48.9% | +16.7% |

El reporte completo (diseño, investigación, resultados mensuales, riesgos) está en el doc
"Atlas BTC/USDC v2 — Estrategia, engine y backtest"; las pruebas de v3 en "Atlas v3 — pruebas de todas las permutaciones".

## Capas

1. **Tendencia** (`engine/signals.py`): 8 señales clásicas → puntaje 0–1.
2. **Ciclo de 4 años** (`engine/portfolio.py`): días 540–900 tras el halving → exposición × 0.5.
3. **Riesgo**: volatilidad objetivo 40% anual con vol realizada de 5 minutos.
4. **ML (solo investigación en v3)** (`engine/ml.py`): el grid de 384 permutaciones mostró efecto nulo,
   así que v3 lo apaga (`ml.enabled: false`). v2 queda en `config/strategy_v2.yaml`.
5. **Interruptor:** si la caída en vivo supera 36%, todo a USDC; reanudar exige `--resume "motivo"`.
6. **Sombra de stablecoins** (`engine/shadow.py`): se registra cada día sin operar; evaluación en oct 2027.
7. Ejecución con banda de 10 puntos, comisión 0.10% + deslizamiento 0.03%.

La configuración está bloqueada en `config/strategy.yaml`.

## Uso

```bash
pip install -r requirements.txt
bash scripts/download_data.sh           # ~400 MB de datos públicos
python -m engine.data                   # minuto -> diario + microestructura
python -m pytest tests/ -q              # pruebas anti look-ahead
python scripts/run_backtest.py --refit  # ML walk-forward (~5 min) + backtest + benchmarks
python scripts/robustness.py            # sensibilidad y Sharpe deflactado
python scripts/paper_trade.py           # paper trading diario + sombra de stablecoins (DefiLlama)
python scripts/test_funding_carry.py --download && python scripts/test_funding_carry.py   # prueba pendiente
python scripts/run_v3_grid.py && python scripts/run_v3_luck.py   # fábrica de pruebas: permutaciones + pruebas de suerte
```

**Paper trading en Hyperliquid:** el plan completo para construirlo con Claude Code está en
`docs/PLAN_paper_trading_hyperliquid.md` (reglas del repo en `CLAUDE.md`).

Toda idea nueva entra con un archivo de pre-registro (ver `config/preregistro_v3.yaml`) antes de correrla.

`scripts/research_*.py` reproducen la investigación que llevó al diseño (solo periodo de desarrollo).

## Estructura

```
config/strategy.yaml     parámetros bloqueados
engine/data.py           carga y agrega datos; detector de ballenas (minutos > p99 de 30 días previos)
engine/features.py       52 features (+9 on-chain opcionales), todas causales
engine/regime.py         HMM de 3 estados con filtro hacia adelante (sin suavizado)
engine/signals.py        capa de tendencia
engine/ml.py             AutoML walk-forward campeón/retador
engine/portfolio.py      combinación de capas, promoción del ML, simulador
engine/strategy.py       ensambla todo desde el YAML
engine/benchmarks.py     BTC, QQQ, IWF, Mag7, Momentum S&P 500 top 10 (membresía histórica)
engine/live.py           paper trading Binance BTCUSDC (no envía órdenes)
tests/                   pruebas anti look-ahead
results/                 salidas del backtest final; results/research = investigación
```

## Advertencias

- Precios de Bitstamp BTC/USD como proxy de Binance BTC/USDC.
- El on-chain de Coin Metrics (repo público) dejó de actualizarse el 2026-05-24; solo se usa en investigación.
- La capa del ciclo es la más frágil (3 ciclos de historia). Sin ella la caída máxima sube a −43%.
- `engine/live.py` no se pudo probar contra la API real de Binance desde el entorno de desarrollo;
  la agregación de 5 minutos sí se validó contra los datos de Bitstamp.
- No es asesoría financiera.
