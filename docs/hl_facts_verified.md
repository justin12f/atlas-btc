# Hechos de Hyperliquid verificados (Fase 0)

Verificación contra la API real el **2026-10-29 (~05:20 UTC)** desde la laptop, con `curl` y
`hyperliquid-python-sdk==0.24.0`. Respuestas grabadas en `tests/fixtures/hl/`. Análisis reproducible:
`python scripts/hl_f0_analysis.py` (solo lee fixtures).

Leyenda: ✅ verificado · ✏️ corregido respecto al plan · ⏸️ no verificable sin cuenta de testnet fondeada
(decisión de Justin del 2026-10-29: no se mueve dinero a testnet por ahora).

## Sección 4 del plan, fila por fila

| Tema | Estado | Evidencia |
|---|---|---|
| Endpoints | ✅ | `POST /info` respondió 200 en mainnet y testnet (`meta`, `spotMeta`, `spotMetaAndAssetCtxs`, `allMids`, `l2Book`, `candleSnapshot`, `userFees`, `fundingHistory`). |
| Par spot UBTC/USDC | ✅ | `spotMeta`: par **`@142`** (índice 142), tokens [UBTC=197, USDC=0], `isCanonical: false`. ID de activo para órdenes = 10142. El id del token (197) ≠ índice del par (142), como decía el plan. Hay un segundo par `@234` UBTC/USDH: no confundir. El SDK también mapea `"UBTC/USDC"` → `@142` en `Info.name_to_coin`. `atlas.hl.markets.resolve_spot` lo resuelve en tiempo de ejecución. Fixture `spot_meta_mainnet.json`. |
| szDecimals | ✅ | UBTC `szDecimals = 5` (`weiDecimals = 10`), USDC 8. Perp BTC `szDecimals = 5`. → tamaño mínimo 0.00001 BTC; precio con ≤ 3 decimales (8 − 5) y ≤ 5 cifras significativas → a ~80,000 el tick es 1 USDC. |
| `spotMetaAndAssetCtxs` | ✏️ (nuevo) | La lista de contextos **no está alineada por posición** con `universe`: hay que cruzar por el campo `coin`. Volumen 24 h de `@142`: 25.3 M USDC (313 UBTC). |
| Velas: límite | ✏️ matiz | `startTime=0` devolvió 5,043 velas de 5m spot (2026-10-11 17:05 → 2026-10-29 05:15), 5,016 de 5m perp y 5,217 de 1m spot. El límite es **≈5,000 (no exacto)**: 5m ≈ 17.5 días, 1m ≈ 3.6 días. Campos `t, T, s, i, o, c, h, l, v, n` como en el plan; `T − t` = intervalo − 1 ms. |
| Velas 1d: historia | ✏️ (nuevo) | 1d spot `@142` trae **toda la vida del par** (633 días completos desde 2025-02-03; < 5,000). 1d perp `BTC` empieza el 2020-08-19, pero antes del lanzamiento las velas tienen `v = 0, n = 0` (precios de referencia, no operaciones de HL). |
| Velas 1d: anclaje | ✅ | Todas las 1d empiezan a las 00:00 UTC. En los 7 días completos 2026-10-22…28, `o`, `c` y `h` de la 1d coinciden **exactamente** con la agregación de las 288 velas de 5m. |
| Libro | ✅ | `l2Book`: `levels = [bids, asks]`, 20 niveles por lado, `{px, sz, n}`, más `coin` y `time` (ms). Spot `@142`: spread 0.12 pb, 668 k USDC en bids y 464 k en asks visibles; impacto de compra 10 k = 0.67 pb, 100 k = 0.83 pb, **1 M no cabe en los 20 niveles**. Perp BTC: spread 0.12 pb, 8.2 M / 3.9 M visibles, 1 M = 0.50 pb. |
| Precios medios | ✅ | `allMids` devuelve dict `coin → str`, incluye `@142`. |
| Funding | ✅ | `fundingHistory` BTC desde `startTime=0` devuelve exactamente **500** registros; el primero es 2023-05-12 00:00 UTC. Cadencia: mediana 1 h (algunos huecos de hasta 8.4 h al inicio). Campos `coin, fundingRate, premium, time`. Hay que paginar (≈20.8 días por llamada). |
| Comisiones base | ✅ | `userFees.feeSchedule`: `spotCross 0.0007`, `spotAdd 0.0004`, `cross 0.00045`, `add 0.00015` = cuadro del plan. Niveles VIP desde 5 M de volumen 14 días (spot 0.06%/0.03%). Ojo: la dirección `0x000…0` aparece en VIP 1 porque tiene volumen; no es el cuadro base. Para una cuenta nueva aplica la base. |
| Comisión: token | ⏸️ | Requiere fills reales en testnet. El PaperBroker aplica la regla del plan (comisión en el activo recibido) y la prueba de ledger la cubre. |
| Redondeo | ✅ (en código) | `round_px`/`round_sz` implementan la regla de la doc; prueba contra la regla del SDK (`round(f"{px:.5g}", 8 − szDecimals)`) en 2,000 precios aleatorios: igual o a 1 tick (el SDK redondea dos veces). Precios enteros siempre válidos. |
| Mínimo de orden 10 USDC | ⏸️ | El mensaje de error exacto solo se ve enviando una orden en testnet. El planificador aplica el mínimo de 10 USDC. |
| Tipos de orden, `cloid`, `schedule_cancel` | ⏸️ | Confirmado en el código del SDK 0.24.0 (`Exchange.order`, `bulk_orders`, `cancel_by_cloid`, `schedule_cancel`, `Info.query_order_by_cloid`; `Cloid` = `0x` + 32 hex). Sin prueba en vivo. |
| Límites de uso | ✅ (en código) | No se puede medir sin provocar 429. `atlas.hl.client` lleva presupuesto de 1,000/min (por debajo de 1,200), peso 2/20 y +1 por cada 60 velas, con reintentos y backoff. |
| API wallets, nonces | ⏸️ | Sin cuenta. Documentado en el RUNBOOK para cuando se active el Modo B. |
| Testnet: pares spot | ✏️ | **No existe UBTC en testnet.** `BTC/USDC = @50` es un mercado roto (szDecimals 0 → mínimo 1 BTC, sin bids, último precio 6,100). **`POBTC/USDC = @2072`** (szDecimals 5) replica el precio de BTC (mid 80,439.5) con libro de 1 nivel × 0.001 por lado: sirve **solo para plomería**. El volumen de testnet se concentra en otros pares (`@1035`). |
| Testnet: faucet | ⏸️ | Requiere depósito previo en mainnet. Fuera de alcance por decisión de Justin. |
| Histórico S3 | — | No revisado (opcional, requester-pays). |
| Restricciones / región VPS | ⏸️ | Requiere acceso al VPS y lectura de los Términos: tarea de Justin. |
| SDK | ✅ | `hyperliquid-python-sdk==0.24.0` se instala en Python 3.14 (con `eth-account 0.13.7`). `Info(...)` hace llamadas de red en su constructor, por eso la lectura usa un cliente propio. |

## Sección 7.4 — fuente de la señal: **SPOT (`UBTC/USDC`, `@142`)**

| Criterio | Umbral | Medido | ¿Pasa? |
|---|---|---|---|
| Volumen diario mediano spot | "alto" | 35.9 M USDC (todo 2025-02-03…2026-10-28); 30.1 M en los últimos 90 días; mínimo de 90 días 6.3 M | ✅ |
| Mediana \|cierre spot / cierre perp − 1\| | ≤ 0.10% | **0.048%** (633 días); p95 0.13%; últimos 90 días 0.035% | ✅ |
| Cociente rv spot / Binance BTCUSDC (5m) | [0.85, 1.15] | **0.985** (17 días completos, 2026-10-12…28) | ✅ |

Datos de apoyo:
- Cierre spot HL vs Bitstamp (serie del backtest): mediana \|dif. log\| **0.017%** en 627 días traslapados (criterio de paridad de datos ≤ 0.10%: pasa con holgura).
- Cierre perp HL vs Bitstamp desde 2023-06: 0.049% (el perp tiene base de funding; el spot sigue mejor al índice del backtest).
- rv: spot/perp 0.97, perp/Binance 1.01, spot/Bitstamp 1.01 y Binance/Bitstamp 1.03 (11 días con Bitstamp). No hay evidencia de ruido de libro delgado en el spot.
- 0 huecos en las velas de 5m de spot, perp y Binance en la ventana.

Limitación: el traslape de rv es de solo 17 días porque HL guarda ~5,000 velas de 5m. La paridad de rv se sigue
midiendo cada semana con el recolector; si la mediana móvil de 30 días saliera de [0.85, 1.15] se abre una
revisión escrita (no se cambia la fuente sin pre-registro).

**Decisión registrada:** señal (precio y rv) con velas spot `@142`; ejecución en spot `@142`. `config/live_hyperliquid.yaml: market.signal_source: spot`.

## Costura de la historia (consecuencia para 7.3)

- Bitstamp (`data/processed/btc_daily.parquet`) termina el **2026-10-22**.
- Velas 1d de HL spot cubren desde 2025-02-03 → los cierres de 2026-10-23 en adelante salen de HL sin hueco.
- Velas 5m de HL disponibles desde 2026-10-11 17:05 → **rv de HL cubre desde 2026-10-12** y no hace falta Binance
  para el hueco, **siempre que el recolector arranque antes de ~2026-11-11** (cuando 2026-10-23 sale de la ventana
  de 5,000 velas). Las velas grabadas como fixtures (`candles_*_5m_full.json`) sirven además de semilla:
  `atlas backfill --from-fixtures` las carga en la base.
