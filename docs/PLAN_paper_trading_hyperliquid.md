# Plan: Atlas v3 en paper trading sobre Hyperliquid

Documento de traspaso para una sesión de Claude Code. Contiene todo lo necesario para construir,
probar y operar la infraestructura de paper trading de la estrategia Atlas v3 usando Hyperliquid
como fuente de datos y como venue de ejecución (simulada en mainnet, real en testnet).

Fecha: 2026-10-29 · Autor del plan: Claude (Cowork) para Justin Franco · Repo base: `btc_quant_v2.zip`

---

## 0. Cómo usar este archivo (Justin)

1. Descarga `btc_quant_v2.zip` y este `.md`. El zip ya trae este plan en `docs/` y un `CLAUDE.md` en la raíz.
2. En tu laptop:
   ```bash
   mkdir -p ~/code && cd ~/code && unzip ~/Descargas/btc_quant_v2.zip && mv btc_quant_v2 atlas-btc && cd atlas-btc
   git init && git add -A && git commit -m "Atlas v3: base del backtest + plan de paper trading"
   gh repo create atlas-btc --private --source . --push      # repo privado nuevo
   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
   python -m pytest tests/ -q                                  # deben pasar 5 pruebas
   ```
   `data/raw/` no viene en el zip (398 MB). Solo hace falta para reconstruir el backtest:
   `bash scripts/download_data.sh && python -m engine.data`. `data/processed/` sí viene y basta para el plan.
3. Abre Claude Code en `~/code/atlas-btc` (tu orquestador) y pega el prompt de la sección 19.
4. Las preguntas abiertas de la sección 20 tienen valores por defecto; no bloquean el arranque.

---

## 1. Contexto: qué es Atlas v3

Estrategia spot BTC/USDC, solo largo o fuera (sin apalancamiento, sin cortos). Cada día a las 00:00 UTC
(18:00 en Ciudad de México) decide qué porcentaje del capital tener en BTC; el resto queda en USDC.

Capas (todas en `engine/`, configuración bloqueada en `config/strategy.yaml`):

| Capa | Regla | Código |
|---|---|---|
| Tendencia | 8 señales binarias (SMA50/100/200, mom30/90/180, Donchian 55/20, EMA20/100) → puntaje 0–1 | `engine/signals.py::trend_score` |
| Ciclo de 4 años | días 540–900 tras el halving → exposición × 0.5 | `engine/portfolio.py::cycle_multiplier`, halvings en `engine/features.py::HALVINGS` |
| Volatilidad objetivo | 40% anual; pronóstico = √(0.5·EWMA_hl10(rv) + 0.5·media30(rv))·√365 con rv = suma de retornos log de 5 min al cuadrado | `engine/portfolio.py::vol_forecast` |
| Banda | solo se opera si \|objetivo − peso actual\| ≥ 10 puntos (más salida total si objetivo < 2% y entrada total si objetivo > 98%) | `engine/portfolio.py::simulate` |
| Interruptor | caída desde el máximo > 36% → todo a USDC; reanudar requiere nota manual | idem |
| ML | apagado en v3 (`ml.enabled: false`); el código queda para investigación | `engine/ml.py` |
| Sombra stablecoins | se registra, no opera; evaluación el 2027-10-25 | `engine/shadow.py` |

Resultados del backtest (walk-forward, costos 0.10% + 0.03%):

| Periodo | Rend. anual | Sharpe | Caída máx. |
|---|---|---|---|
| Completo 2018-01-01 → 2026-10-22 | 30.9% | 1.20 | −23.9% |
| Desarrollo 2018-01 → 2025-10-22 | 34.9% | 1.27 | −23.9% |
| Holdout 2025-10-23 → 2026-10-22 | +3.4% | 0.39 | −5.0% |
| BTC buy & hold (completo) | 22.7% | 0.64 | −81.5% |

Otros datos útiles: exposición promedio ~35%, rotación ~5.5× al año, 339 operaciones en 8.8 años
(~38 rebalanceos/año), Sharpe deflactado 94% con 414 variantes probadas, robustez: ejecutar 1 día tarde → 29.6%,
costos × 2.5 → 29.5%. La capa del ciclo es la más frágil (3 ciclos de historia; sin ella la caída máx. sube a −43%).
Objetivo al cierre del 2026-10-22: 75% BTC.

Documentos de referencia (privados en claude.ai, para Justin; Claude Code no los necesita):
reporte v2 https://claude.ai/artifact/WvLgxhZ44McxJcntWPQMYm ·
investigación de estrategias https://claude.ai/artifact/Tjt5qvHBLvRHBTTWvreRn6 ·
papers https://claude.ai/artifact/45V6JEnUPcumkMkYX1B2Fq ·
permutaciones v3 https://claude.ai/artifact/QWWFkJf6s2YeweTMLdSTdW

---

## 2. Objetivo y lo que NO se hace

**Objetivo.** Correr Atlas v3 todos los días durante ~12 meses sin dinero real, con datos de Hyperliquid,
de forma desatendida en el VPS, y medir si la implementación en vivo reproduce el backtest:
mismas señales, costos reales iguales o menores a los supuestos, cero fallas operativas sin explicar.

**Para qué sirve el paper trading (importante).** Un año no alcanza para probar que la estrategia tiene
ventaja: con Sharpe ~1.2 el error estándar del Sharpe de un año es ~1. Lo que el paper trading sí puede
probar es la *implementación*: paridad de señales, costos, deslizamiento, datos y operación. El rendimiento
del año se reporta, pero no es criterio de aprobación (ver sección 15).

**No se hace en este plan:**
- No se envían órdenes con dinero real. El código de mainnet queda como stub bloqueado.
- No se cambia ningún parámetro de `config/strategy.yaml`. Toda idea nueva requiere pre-registro (`config/preregistro_v3.yaml` es el modelo).
- No se reimplementa la lógica de la estrategia: la capa operativa llama a `engine/`.
- No se usa apalancamiento, cortos ni perpetuos para la posición. El perpetuo se usa solo como fuente de datos / investigación.

---

## 3. Decisiones tomadas y supuestos

| Tema | Decisión |
|---|---|
| Venue | Hyperliquid para todo: datos de mercado (mainnet), simulación de llenados (libro real de mainnet), plomería de órdenes (testnet). |
| Instrumento | Spot `UBTC/USDC` (BTC tokenizado por Unit, en HyperCore desde feb-2025). El nombre interno del par es `@{índice}`: se resuelve en tiempo de ejecución con `spotMeta`, nunca se escribe a mano. |
| Fuente de la señal | Por decidir en la Fase 0 con criterio medible: velas spot `UBTC/USDC` o velas del perpetuo `BTC` (más líquido). Ejecución siempre en spot. |
| Historia | Antes de que existan datos de Hyperliquid se usa la serie de Bitstamp de `data/processed/btc_daily.parquet`, cosida y marcada con columna `source`. |
| Capital de papel | 10,000 USDC (mismo valor que `engine/live.py`). |
| Infra | Desarrollo en la laptop (Ubuntu, ASUS Zenbook 14, 32 GB). Producción en el VPS de Hostinger con systemd. |
| Horario | Decisión con la vela diaria que cierra a las 00:00 UTC; ejecución 00:06–00:40 UTC. |
| Llaves | El paper en mainnet no necesita llaves (solo datos públicos). Testnet usa una API wallet (agente) sin permiso de retiro. |
| Dinero real | No durante ~12 meses. La decisión posterior es un documento aparte (sección 15.4). |

---

## 4. Hechos de Hyperliquid que el código debe respetar

Investigados el 2026-10-28/29 en la documentación oficial y en el SDK `hyperliquid-python-sdk==0.24.0`.
La API no era alcanzable desde el entorno donde se escribió el plan, así que **la Fase 0 verifica cada fila
contra la API real** y guarda las respuestas como fixtures.

| Tema | Dato |
|---|---|
| Endpoints | Mainnet `https://api.hyperliquid.xyz`, testnet `https://api.hyperliquid-testnet.xyz` (constantes `MAINNET_API_URL`, `TESTNET_API_URL` en `hyperliquid.utils.constants`). Todo es `POST /info` (lectura) y `POST /exchange` (firmado). WebSocket en `/ws`. |
| Velas | `candleSnapshot` (`Info.candles_snapshot(name, interval, startTime, endTime)`); intervalos 1m,3m,5m,15m,30m,1h,2h,4h,8h,12h,1d,3d,1w,1M; **solo las 5,000 velas más recientes** por intervalo → 5m ≈ 17.4 días, 1m ≈ 3.5 días. No hay velas en el S3 histórico: lo que no se recolecta se pierde. Campos: `t` (inicio ms), `T` (fin), `o,h,l,c,v` (strings), `n` (trades), `s`, `i`. |
| Libro | `l2Book` (`Info.l2_snapshot(name)`): hasta 20 niveles por lado, `levels = [bids, asks]`, cada nivel `{px, sz, n}`. |
| Precios medios | `allMids` (`Info.all_mids()`). |
| Metadatos | `meta` (perpetuos, BTC = índice 0) y `spotMeta` (`universe` con `name`, `tokens`, `index`; `tokens` con `szDecimals`). ID de activo spot = 10000 + índice spot (el SDK lo arma en `Info.__init__`, `coin_to_asset`, `name_to_coin`). El id del token ≠ índice del par. |
| Funding | Se paga cada hora (1/8 de la tasa de 8 h). F = prima promedio + clamp(interés − prima, −0.05%, +0.05%), interés 0.01%/8 h, tope 4%/hora. `fundingHistory` (`Info.funding_history(name, startTime, endTime)`) devuelve ≤ 500 elementos por respuesta → paginar. |
| Comisiones base | Spot: taker 0.070%, maker 0.040%. Perps: taker 0.045%, maker 0.015%. El volumen spot cuenta doble para los niveles; staking de HYPE da 5–40% de descuento. Verificar el nivel real con `Info.user_fees(address)`. El backtest supone 0.13% por lado (0.10% + 0.03%), así que el spot de HL es más barato que lo supuesto. |
| Comisión: token | En spot la comisión se cobra en el activo recibido (verificar con el campo `feeToken` de los fills en testnet). |
| Redondeo | Precio: ≤ 5 cifras significativas y ≤ (8 − szDecimals) decimales en spot (6 − szDecimals en perps); precios enteros siempre válidos. Tamaño: redondear a `szDecimals` del token base. |
| Mínimo | Valor mínimo por orden de 10 USDC (verificar mensaje de error en testnet). |
| Tipos de orden | Límite con `tif` = `Alo` (post-only), `Ioc`, `Gtc`. `Exchange.market_open` es un IOC agresivo con deslizamiento por defecto 5% (no usarlo tal cual). `cloid` para idempotencia. `Exchange.schedule_cancel(t)` = interruptor de hombre muerto (máx. 10 disparos/día). |
| Límites de uso | 1,200 de peso por minuto por IP. Peso 2: `l2Book`, `allMids`, `clearinghouseState`, `spotClearinghouseState`, `orderStatus`; peso 20: la mayoría del resto; `candleSnapshot` suma peso por cada 60 elementos devueltos. WS: máx. 10 conexiones y 1,000 suscripciones. Límite por dirección: 1 petición por cada 1 USDC operado + 10,000 de colchón. |
| API wallets (agentes) | Firman, no retiran. Se aprueban con `Exchange.approve_agent(name)` firmado por la cuenta maestra (o desde la UI). Las consultas `/info` se hacen con la dirección **maestra**, no la del agente. Un agente por proceso. No reutilizar direcciones de agentes dados de baja. Pueden ser podados. |
| Nonces | Se guardan los 100 nonces más altos por firmante; uno nuevo debe ser mayor que el menor de ese conjunto y no repetido, y estar en (T − 2 días, T + 1 día). El SDK usa milisegundos → el reloj del VPS debe estar sincronizado (chrony). |
| Testnet | Faucet de 1,000 USDC de prueba en `https://app.hyperliquid-testnet.xyz/drip`, solo para direcciones que ya depositaron en mainnet. Los mercados spot de testnet pueden no incluir BTC: verificar. |
| Histórico S3 | `s3://hyperliquid-archive` (snapshots L2, `asset_ctxs` en .csv.lz4, requester-pays) y `s3://hl-mainnet-node-data`. Sin velas. Opcional. |
| Restricciones | Excluidos: EE. UU., Ontario y territorios sancionados. México no aparece en la lista; revisar los Términos antes de cualquier operación real. Revisar también en qué país está el VPS de Hostinger. |
| Custodia | UBTC lo emite Unit con una red Guardian MPC 2-de-3. Riesgo irrelevante en papel; relevante para la decisión de dinero real. |
| SDK | `pip install hyperliquid-python-sdk==0.24.0` (depende de `eth-account`, `eth-utils`, `msgpack`, `requests`, `websocket-client`). `Info(base_url, skip_ws=True)` para no abrir WebSocket. |

---

## 5. Arquitectura

```
                    Hyperliquid MAINNET (solo lectura, sin llaves)
     candleSnapshot 5m/1m/1d · l2Book · allMids · spotMeta · fundingHistory
                                   │
                    ┌──────────────▼───────────────┐
                    │  atlas.data (recolectores)   │  cada hora :03 + 00:02 UTC
                    │  SQLite WAL data/live/atlas.db│  chequeos de calidad, relleno de huecos
                    └──────────────┬───────────────┘
   historia Bitstamp ──► stitch ──►│ barras diarias + rv (columna source)
                                   ▼
                    ┌──────────────────────────────┐
                    │ atlas.signal → engine/*      │  00:05 UTC, idéntico al backtest
                    │ objetivo del día + hash cfg  │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ atlas.risk (interruptor 36%, │  si algo falla: no operar + alerta
                    │ datos viejos, divergencia)   │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ atlas.execution.planner      │  banda, tamaño, redondeos, mínimo 10 USDC
                    └───────┬──────────────┬───────┘
              Modo A        │              │        Modo B
     PaperBroker (libro     │              │   TestnetBroker (órdenes reales en
     real de mainnet,       ▼              ▼   testnet con API wallet)
     modelo de llenado)  ledger ◄──── reconciliación con spotClearinghouseState
                            │
                            ▼
              reportes diarios/semanales · alertas Telegram · healthchecks · respaldos
```

Modos:
- **A — Papel en mainnet (principal).** Datos y libro reales; llenados simulados. Es el que mide costos y paridad.
- **B — Testnet (plomería).** Mismo planificador, órdenes reales en testnet con montos chicos. Prueba firma, nonces, redondeos, `cloid`, cancelaciones, llenados parciales y reconciliación. Su rendimiento no significa nada (liquidez de testnet).
- **C — Mainnet real.** Fuera de alcance. `LiveBroker` existe solo como clase que lanza error.

A y B corren en paralelo con ledgers separados.

---

## 6. Estructura nueva del repo

`engine/` no se toca salvo correcciones con prueba. La operación vive en un paquete nuevo `atlas/`.

```
atlas/
  __init__.py
  config.py             # carga config/live_hyperliquid.yaml + variables de entorno (secretos)
  interfaces.py         # Protocols: MarketData, Broker, Store, Clock (contrato entre terminales)
  hl/
    client.py           # envoltura de hyperliquid.info.Info (skip_ws=True), presupuesto de peso, reintentos con backoff
    markets.py          # resolver "UBTC/USDC" -> "@N", szDecimals, round_px(), round_sz()
  data/
    store.py            # SQLite WAL + migraciones simples (schema_version)
    collectors.py       # velas 5m/1m/1d spot y perp, l2Book, funding, allMids
    stitch.py           # Bitstamp + HL -> serie diaria continua con source y rv
    quality.py          # barras faltantes (288 de 5m por día), outliers, precio vs referencia
  signal.py             # barras diarias -> engine.strategy.build_targets(d, None, C, pcfg) -> objetivo
  risk.py               # interruptor, datos viejos, divergencia de precio, tamaño máximo, modo seguro
  execution/
    planner.py          # delta, banda, tamaño, redondeos, mínimo
    fillsim.py          # modelos taker (recorrer libro) y maker-first (velas 1m)
    brokers.py          # Broker, PaperBroker, TestnetBroker, LiveBroker (bloqueado)
    ledger.py           # saldos UBTC/USDC, comisión por feeToken, equity, máximo, caída, pausa
  reports.py            # diario, semanal, mensual, paridad con backtest
  alerts.py             # Telegram + healthchecks.io
  cli.py                # atlas collect | daily | resume | status | report | replay | backfill
config/live_hyperliquid.yaml
config/preregistro_paper.yaml     # protocolo de evaluación, escrito ANTES del día 1
deploy/
  install.sh
  atlas.env.example
  systemd/atlas-collect.{service,timer} atlas-daily.{service,timer} atlas-weekly.{service,timer} atlas-backup.{service,timer}
docs/
  PLAN_paper_trading_hyperliquid.md   # este archivo
  hl_facts_verified.md                # salida de la Fase 0
  RUNBOOK.md                          # qué hacer ante cada alerta
tests/
  fixtures/hl/*.json                  # respuestas reales grabadas en la Fase 0
  test_markets.py test_planner.py test_ledger.py test_fillsim.py test_signal_parity.py
  test_stitch.py test_idempotency.py test_faults.py test_no_lookahead.py (ya existe)
```

`engine/live.py` y `scripts/paper_trade.py` (Binance) quedan como legado; `engine/shadow.py` se reutiliza tal cual.

Configuración propuesta (`config/live_hyperliquid.yaml`):

```yaml
mode: paper                 # paper | testnet | both   (live no existe en este plan)
market:
  spot_pair: "UBTC/USDC"    # se resuelve a "@N" con spotMeta al arrancar
  perp_coin: "BTC"
  signal_source: auto       # auto | spot | perp  (decisión de la Fase 0)
paper:
  initial_usdc: 10000
  fill_policy: maker_first  # maker_first | taker
  maker_timeout_min: 30
  reprice_every_min: 5
  max_slippage: 0.005       # tope para el IOC de respaldo
  capacity_probe_usd: [10000, 100000, 1000000]   # solo se mide, no entra al ledger
testnet:
  initial_usdc: 1000
  max_order_usd: 200
fees:                       # nivel base; se sobrescribe con Info.user_fees si hay dirección
  spot_taker: 0.00070
  spot_maker: 0.00040
  perp_taker: 0.00045
  perp_maker: 0.00015
risk:                       # el umbral del interruptor se LEE de config/strategy.yaml
  max_data_age_min: 20
  max_ref_divergence: 0.01  # |precio HL / referencia - 1|
  min_order_usd: 10
  max_late_hours: 23        # si la corrida diaria llega más tarde, se usa la señal del día siguiente
data:
  db: data/live/atlas.db
  history_seed: data/processed/btc_daily.parquet
alerts:
  telegram: true
  healthchecks: true
  drawdown_levels: [0.15, 0.25, 0.36]
```

---

## 7. Datos

### 7.1 Tablas (SQLite, `data/live/atlas.db`, modo WAL)

| Tabla | Llave | Columnas principales |
|---|---|---|
| `candles` | (coin, interval, t) | o,h,l,c,v,n, fetched_at, source (`hl`/`binance`/`bitstamp`) |
| `book_snapshots` | (coin, ts) | bids_json, asks_json, mid, spread_bps, depth_10k_bps, depth_100k_bps |
| `funding` | (coin, time) | funding_rate, premium |
| `daily_bars` | date | open,high,low,close,volume, rv, rv_down, n_5m, source, quality_flags |
| `signals` | (date, mode) | trend, cycle_mult, vol_fc, vol_scaler, target, config_hash, code_version, computed_at |
| `orders` | cloid | mode, date, side, px, sz, tif, status, exchange_oid, created_at, updated_at |
| `fills` | (mode, fill_id) | cloid, px, sz, fee, fee_token, liquidity (maker/taker), ts |
| `ledger` | (mode, date) | usdc, ubtc, mark_px, equity, peak, drawdown, paused, weight |
| `runs` | run_id | job, started_at, finished_at, status, error |
| `events` | id | ts, level, kind, message, payload_json (alertas, pausas, reanudaciones) |
| `shadow` | date | igual que `data/live/shadow_stablecoins.csv` |

### 7.2 Recolección

- **Cada hora (:03):** velas 5m y 1m de spot `UBTC/USDC` y perp `BTC` (ventana: desde la última vela guardada − 2 velas, para corregir la vela abierta), vela 1d, `l2Book` spot y perp, `allMids`, `fundingHistory` BTC de la última hora. Peso total por corrida < 200.
- **00:02 UTC:** recolección final del día anterior y chequeo de calidad: 288 velas de 5m, sin huecos > 10 min, sin saltos > 8% en 5 min sin confirmación del perp.
- **Huecos:** si faltan velas y aún están dentro de las 5,000 más recientes, se rellenan desde HL. Si ya no están, respaldo con Binance BTCUSDC 5m (`data.binance.vision` o REST) marcado `source=binance` + evento de alerta. Por eso el recolector se despliega en el VPS **lo antes posible** (Fase 1), aunque el resto no esté listo.
- **Respaldo de historia de funding:** `backfill funding` pagina `fundingHistory` desde el inicio del perp BTC (≤ 500 por respuesta ≈ 20.8 días por llamada).

### 7.3 Costura de la historia

La señal necesita ~420 días (SMA200, mom180, Donchian 55, EMA100, vol de 30 días).

1. Precio de cierre diario: Bitstamp (`data/processed/btc_daily.parquet`) hasta el primer día completo de HL en la fuente elegida; HL después. Velas 1d de HL ancladas a 00:00 UTC (verificar en Fase 0 comparando con la agregación de 5m).
2. `rv` y `rv_down`: Bitstamp 5m hasta el día en que empieza la recolección propia de 5m de HL; HL después. Si hace falta cubrir el hueco entre ambas, Binance BTCUSDC 5m.
3. Cada fila guarda `source`. Una prueba compara, en el periodo donde se traslapan, el cierre de HL contra Bitstamp/Binance (mediana de \|dif. log\| ≤ 0.10%) y el cociente de rv (mediana dentro de [0.85, 1.15]). Si el rv de HL sale sistemáticamente más alto (ruido de libro delgado), la exposición bajaría sin razón: eso decide la fuente de la señal.

### 7.4 Fuente de la señal (decisión de la Fase 0)

Criterio, medido sobre el traslape disponible (spot desde feb-2025):
- Elegir **spot** si su volumen diario mediano es alto, la mediana de \|cierre spot / cierre perp − 1\| ≤ 0.10% y el cociente de rv spot/Binance está en [0.85, 1.15].
- Si no, **perp** para la señal (precio y rv) y spot solo para ejecutar.
- Registrar la decisión y los números en `docs/hl_facts_verified.md`. No se cambia después sin pre-registro.

---

## 8. Flujo diario (UTC)

| Hora | Paso |
|---|---|
| 00:02 | `atlas collect --final` del día anterior + chequeos de calidad. |
| 00:05 | `atlas daily`: arma barras diarias, calcula el objetivo con `engine`, lo guarda en `signals` con `config_hash` (sha256 de `strategy.yaml`) y versión de código (git sha). |
| 00:05 | Riesgo: datos ≤ 20 min, divergencia HL vs referencia ≤ 1%, interruptor (caída con el cierre del día). Si falla un chequeo → no se opera, se mantiene la posición, alerta. El interruptor sí manda a USDC. |
| 00:06 | Planificador: si \|objetivo − peso\| ≥ 10 pts (o salida total) → orden; si no, nada. |
| 00:06–00:36 | Ejecución maker-first: límite post-only (`Alo`) en el mejor bid (compra) o ask (venta); se reprecia cada 5 min. |
| 00:36 | Lo no llenado va como IOC con precio tope = mejor contraparte × (1 ± 0.5%). |
| 00:40 | Ledger, reconciliación (testnet), reporte diario, ping a healthchecks, Telegram. |
| Lunes 01:00 | Reporte semanal. Día 1 de cada mes: réplica de paridad contra el backtest. |
| 02:00 | Respaldo de la base de datos. |

00:00 UTC = 18:00 en Ciudad de México.

**Idempotencia:** `atlas daily` trabaja siempre sobre "el último día completo". Si ese día ya tiene ledger, no hace nada.
Si la corrida anterior quedó a medias (órdenes abiertas), primero reconcilia y continúa. Un candado de archivo (`flock`)
impide dos procesos a la vez. Si la corrida llega más de 23 h tarde, se usa la señal del día nuevo (el backtest mostró que 1 día de retraso cuesta poco).

---

## 9. Ejecución

### 9.1 Planificador
1. `w = ubtc·mark / equity`; `e` = objetivo (0 si pausado).
2. Operar si `|e − w| ≥ 0.10`, o (`e < 0.02` y `w ≥ 0.02`), o (`e > 0.98` y `w ≤ 0.98` y `|e − w| > 0.02`).
   Es exactamente la regla de `engine/portfolio.py::simulate`. Ojo: el legado `engine/live.py::paper_step` no tiene la
   tercera condición (entrada a 100%); el planificador nuevo debe seguir a `simulate` y una prueba debe comparar ambos.
   Lo ideal es extraer la regla a una función pura en `engine/portfolio.py` (p. ej. `should_trade(e, w, band)`) usada por
   `simulate` y por el planificador, con prueba de que el backtest no cambia.
3. `notional = (e − w) · equity`; si `|notional| < 10 USDC` no se opera.
4. Tamaño en UBTC redondeado hacia abajo a `szDecimals`; precio con `round_px` (5 cifras significativas, ≤ 8 − szDecimals decimales).
5. En ventas, nunca vender más UBTC del saldo; en compras, reservar la comisión.

### 9.2 Modelos de llenado del PaperBroker
- **Taker:** recorre el `l2Book` de mainnet del momento (hasta 20 niveles) y calcula VWAP + comisión taker 0.070%. Si el libro no alcanza, se marca falta de liquidez y se llena lo posible.
- **Maker-first (política por defecto):** la orden simulada se considera llenada al precio límite solo si en las velas de 1m posteriores el precio **cruza** el límite al menos 1 tick (low < bid en compra, high > ask en venta); tocarlo no basta (cola). Comisión maker 0.040%. A los 30 min, lo restante se llena como taker con el libro de ese momento.
- Para cada orden se guarda también el costo hipotético 100% taker y el "costo backtest" (cierre de 00:00 + 0.13%), para comparar.
- **Prueba de capacidad:** se calcula (sin entrar al ledger) el VWAP para 10k, 100k y 1M USDC.

### 9.3 Métrica de costo
Implementation shortfall por orden = signo × (VWAP / cierre de 00:00 − 1) + comisión. Se compara contra 0.13% del backtest.

### 9.4 TestnetBroker
- `Exchange(agent_wallet, TESTNET_API_URL, account_address=master)`; consultas con la dirección maestra.
- `cloid` determinista por (fecha, modo, intento). Antes de reenviar, consultar `query_order_by_cloid`.
- `schedule_cancel(now + 45 min)` al iniciar la ventana de ejecución.
- Reconciliación: saldos del ledger de testnet vs `spot_user_state(master)`; cualquier diferencia > 1e-8 UBTC o > 0.01 USDC → alerta y pausa de testnet.
- Si testnet no tiene par spot de BTC, usar el par spot más líquido de testnet solo para probar la plomería. No usar perps.

---

## 10. Riesgo y salvaguardas

- **Interruptor de caída:** caída del equity de papel desde el máximo > 36% (umbral leído de `strategy.yaml`) → objetivo 0, `paused = true`, evento y alerta. Reanudar solo con `atlas resume --mode paper --note "motivo"`; el máximo se reinicia al reanudar (igual que `engine/live.py::resume`).
- **Modo seguro (no operar, mantener posición):** datos viejos, barras incompletas, divergencia > 1% vs referencia (perp de HL y, como segunda referencia, Binance/Coinbase público), `spotMeta` cambió (szDecimals o índice), error de paridad de señal, error de reconciliación.
- **Alertas de caída:** −15%, −25% y −36%.
- **Tamaño máximo:** ninguna orden puede superar el equity del modo; en testnet además ≤ 200 USDC.
- **Mainnet bloqueado:** `LiveBroker.__init__` lanza `RuntimeError`. Ninguna variable de entorno lo habilita en este plan.
- **Reloj:** chrony activo; si el desfase es > 1 s se alerta (nonces y horarios dependen de él).

---

## 11. Seguridad y llaves

- El modo A no usa llaves.
- Para testnet: crear una cuenta (dirección) dedicada solo a Atlas. La llave maestra **nunca** va al VPS ni al repo. Aprobar el agente desde la laptop o la UI de testnet y guardar en el VPS solo la llave del agente.
- Secretos en `/etc/atlas/atlas.env` (dueño `atlas`, permisos 600), cargados con `EnvironmentFile=` de systemd: `HL_TESTNET_ACCOUNT`, `HL_TESTNET_AGENT_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HEALTHCHECKS_URL`.
- Un agente por proceso; no reutilizar direcciones de agentes dados de baja; registrar en `events` cada aprobación.
- `.gitignore` con `data/live/`, `*.env`, `*.key`; `gitleaks` como pre-commit y en CI.
- Logs sin llaves ni firmas.
- El VPS también hospeda leoncaviec.com: usuario de sistema `atlas` sin shell de login, sin sudo, directorio propio, firewall sin puertos nuevos (todo es saliente).

---

## 12. Despliegue en el VPS (Hostinger)

1. Verificar región del VPS (si está en EE. UU., documentarlo y evaluar mover el job a un VPS fuera de EE. UU. antes de testnet/real).
2. `timedatectl set-timezone UTC` no es obligatorio: los timers usan `OnCalendar=... UTC`. Instalar y verificar chrony.
3. `deploy/install.sh`: crea usuario `atlas`, `/opt/atlas` (código, venv Python ≥ 3.11), `/var/lib/atlas` (datos, `ATLAS_DATA_DIR`), `/etc/atlas/atlas.env`.
4. Timers systemd:
   - `atlas-collect.timer`: `OnCalendar=*-*-* *:03:00 UTC`, `Persistent=true`.
   - `atlas-daily.timer`: `OnCalendar=*-*-* 00:05:00 UTC`, `Persistent=true`.
   - `atlas-weekly.timer`: `OnCalendar=Mon *-*-* 01:00:00 UTC`.
   - `atlas-backup.timer`: `OnCalendar=*-*-* 02:00:00 UTC` → `sqlite3 .backup` + gzip, 30 días locales, copia a la laptop (rsync) o a Google Drive (rclone).
   - Servicios `Type=oneshot`, `TimeoutStartSec=50min`, `MemoryMax=1G`, `Nice=10`.
5. Monitoreo: ping a healthchecks.io al final de cada corrida diaria (alerta por correo si no llega); Telegram para el resumen diario, operaciones, errores y pausas.
6. Actualizaciones: `git pull` + `pip install -r requirements.txt` + `pytest` antes de reiniciar timers; nunca editar código en el VPS.
7. `docs/RUNBOOK.md`: síntoma → causa probable → acción, para cada alerta.

---

## 13. Pruebas

| Tipo | Qué prueba |
|---|---|
| Unitarias | `round_px`/`round_sz` contra casos de la doc y fixtures; mínimo 10 USDC; banda; interruptor y reanudación; comisión por `feeToken`; invariantes del ledger (sin saldos negativos, conservación de valor menos comisiones). |
| Anti look-ahead | Las existentes + nueva: la señal del día t con datos truncados en t es idéntica a la calculada con datos completos. Barras diarias solo con velas `t < fin del día`. |
| Paridad de señal | Para cada día de la réplica, `atlas.signal` == `engine.strategy.build_targets` sobre la misma serie cosida (tolerancia 1e-9). |
| Réplica | `atlas replay --from 2025-03-01 --to 2026-10-22 --broker fake`: recorre el pipeline día por día con datos truncados y debe reproducir `engine.portfolio.simulate` (mismos días de operación, objetivos idénticos; equity dentro de 1e-3 relativo — ver §22). |
| Paridad de datos | HL vs Bitstamp/Binance: cierre y rv (sección 7.3). |
| Fallas | Timeout, 429, respuesta vacía, velas faltantes, libro viejo, corrida duplicada (no duplica operaciones), proceso muerto a mitad de ejecución (reanuda reconciliando), reinicio del VPS, desfase de reloj. |
| Integración testnet | Marcadas `@pytest.mark.testnet`, se corren a mano: ciclo completo compra→venta, parcial, cancelación, reprecio, `schedule_cancel`. |
| CI | GitHub Actions: `ruff` + `pytest -m "not testnet"` en cada push. |

---

## 14. Fases con criterios de aceptación

| Fase | Entregable | Aceptación |
|---|---|---|
| **F0 Verificación** (1–2 días) | Repo git privado, `CLAUDE.md`, `atlas/interfaces.py`, esquema de base de datos, `docs/hl_facts_verified.md`, fixtures en `tests/fixtures/hl/`. Verificar cada fila de la sección 4 contra la API (spotMeta: índice de UBTC/USDC y szDecimals; comisiones; límites de velas; anclaje de 1d; profundidad y spread de spot y perp; pares spot en testnet; mínimo de orden). | Cada fila verificada o corregida con evidencia; decisión de fuente de señal registrada (7.4). |
| **F1 Datos** | `atlas.hl.client`, `atlas.data.*`, `atlas collect`, `atlas backfill`. Recolector desplegado en el VPS **al terminar la fase** aunque lo demás no exista. | 7 días seguidos sin huecos (o rellenados automáticamente); reporte de paridad HL vs Bitstamp/Binance. |
| **F2 Señal** | `atlas.signal`, costura, tabla `signals`. | Paridad 100% en la réplica de los últimos 400 días; diferencia vs objetivos del backtest en días traslapados reportada y explicada por la fuente de precio. |
| **F3 Papel (Modo A)** | Planner, fillsim, PaperBroker, ledger, riesgo, CLI (`daily`, `resume`, `status`). | Pruebas unitarias, réplica e idempotencia en verde. |
| **F4 Testnet (Modo B)** | TestnetBroker, reconciliación, agente. | 20 ciclos seguidos sin error, incluyendo parciales y cancelaciones; ledger == saldos del exchange. |
| **F5 Operación** | systemd, secretos, alertas, healthchecks, respaldos, RUNBOOK, CI. | 7 días desatendidos con reporte diario; simulacro: matar el proceso a mitad de ejecución y reiniciar el VPS sin duplicar operaciones. |
| **F6 Reportes** | Diario/semanal/mensual (`reports/` en markdown + CSV; opcional una página HTML estática). | Reporte semanal automático con: equity A y B, operaciones, costo por orden vs 0.13%, paridad, uptime, sombra de stablecoins. |
| **F7 Investigación conectada** | Descarga completa de funding de HL; correr `scripts/test_funding_carry.py` con el protocolo pre-registrado; HL 2023–2026 como confirmación fuera de muestra. | Resultado documentado. No cambia la estrategia en vivo. |

Orden recomendado: F0 → F1 (y desplegar el recolector) → F2 → F3 → F5 → **inicio del periodo de evaluación** → F4 y F6 en paralelo → F7.

---

## 15. Protocolo de evaluación (pre-registrar en `config/preregistro_paper.yaml` antes del día 1)

### 15.1 Periodo
Día 1 = primer día después de aprobar F5. Revisiones a los 3, 6 y 12 meses. Ningún parámetro de la estrategia cambia durante el periodo.

### 15.2 Criterios de aprobación (implementación)
| Métrica | Umbral |
|---|---|
| Días con decisión ejecutada a tiempo | ≥ 99% (≤ 3 fallas al año), 0 operaciones duplicadas |
| Paridad de señal (vivo vs recálculo offline) | 100% de los días |
| Discrepancias de ledger sin explicar | 0 |
| Costo por orden (shortfall + comisión), mediana | ≤ 0.13% (supuesto del backtest) |
| Costo por orden, percentil 90 | ≤ 0.30% |
| Diferencia anual equity papel vs réplica del backtest con datos HL | ≤ 1.5 puntos y explicada por costos/horario |
| Datos: \|cierre HL vs referencia\|, mediana | ≤ 0.10% |
| Datos: cociente de rv HL / referencia, mediana | en [0.85, 1.15] |

### 15.3 Rendimiento (solo informativo)
Se reporta contra la distribución de 12 meses del backtest (`results/atlas_v3/robustness.json`). Si queda por debajo del
percentil 5, se abre una revisión escrita; no se abandona ni se ajusta automáticamente. ~38 rebalanceos al año dan
muestra suficiente para medir costos, no para medir ventaja.

### 15.4 Después de 12 meses
Decisión aparte, en un documento propio: Términos de Hyperliquid para México, región del VPS, riesgo de custodia de UBTC (Unit)
y del puente de USDC, capital inicial pequeño y escalonado, y solo si todos los criterios de 15.2 pasaron.

---

## 16. Pendientes de investigación que se conectan

- **Funding carry (F7):** el protocolo está en `scripts/test_funding_carry.py` (F0 base, F1 carry con capital ocioso, F2 alarma de funding > 30% anual, F3 ambos; criterio Reality Check p < 0.05 en 2020–2025 y no peor en el último año). La historia 2020–2025 viene de Binance (`--download`); HL aporta 2023–2026 fuera de muestra. Ojo: F1 implica un corto en perpetuo como cobertura; aunque sea neutral en delta, choca con la regla "sin cortos" y requiere aprobación explícita de Justin antes de cualquier uso en vivo.
- **Sombra de stablecoins:** seguir registrando con `engine/shadow.py` (DefiLlama) cada día desde el VPS; evaluación el 2027-10-25 con el criterio de `strategy.yaml`.
- **Halving 2028:** `engine/features.py::HALVINGS` tiene 2028-04-15 estimado; actualizar con la fecha real del bloque 1,050,000. La ventana bajista del ciclo actual (540–900 días tras 2024-04-20) terminó el 2026-10-07; el multiplicador vuelve a aplicar a partir de ~oct-2029.

---

## 17. Reparto para el orquestador (4 terminales vía MCP)

El orquestador hace F0 solo y fija los contratos (`atlas/interfaces.py`, esquema de la base, config). Después:

| Terminal | Rama | Alcance | Directorios propios |
|---|---|---|---|
| T1 Datos | `feat/data` | F1 + paridad de datos + costura | `atlas/hl/`, `atlas/data/` |
| T2 Señal y ejecución | `feat/exec` | F2 + F3 (planner, fillsim, brokers, ledger, riesgo) | `atlas/signal.py`, `atlas/execution/`, `atlas/risk.py` |
| T3 Operación | `feat/ops` | F5 (systemd, install, secretos, alertas, respaldos, CI, RUNBOOK) con dobles de prueba; despliega el recolector en cuanto T1 termine | `deploy/`, `atlas/alerts.py`, `.github/`, `docs/RUNBOOK.md` |
| T4 QA e investigación | `feat/qa` | Pruebas de fallas, réplica, paridad de señal, luego F4 testnet, F6 reportes, F7 funding | `tests/`, `atlas/reports.py`, `scripts/` |

Reglas: cada terminal toca solo sus directorios; los cambios a `interfaces.py` o al esquema los aprueba el orquestador;
nada se fusiona a `main` sin `pytest` en verde; `engine/` y `config/strategy.yaml` son de solo lectura para todos.

---

## 18. `CLAUDE.md` (ya incluido en la raíz del zip)

```markdown
# Atlas BTC/USDC — reglas para Claude Code

## Qué es
Estrategia spot BTC/USDC (largo o fuera, sin apalancamiento ni cortos), decisión diaria 00:00 UTC.
`engine/` = estrategia y backtest. `atlas/` = operación en vivo sobre Hyperliquid (paper + testnet).
Plan completo: docs/PLAN_paper_trading_hyperliquid.md

## Reglas duras
1. `config/strategy.yaml` y la lógica de `engine/` están bloqueadas. No cambiar parámetros. Ideas nuevas: pre-registro primero (modelo: config/preregistro_v3.yaml). Única excepción: refactors sin cambio de resultado (p. ej. extraer la regla de banda a una función pura), aprobados por el orquestador y con prueba de que `scripts/run_backtest.py` da exactamente los mismos números.
2. `atlas/` nunca reimplementa señales: llama a `engine.strategy.build_targets` / `engine.signals` / `engine.portfolio`.
3. Nada envía órdenes a mainnet. `LiveBroker` lanza error. Solo testnet con API wallet.
4. Nunca escribir llaves, direcciones maestras ni tokens en el repo, logs o pruebas. Secretos solo por variables de entorno.
5. Nombres de pares spot de Hyperliquid (`@N`) y szDecimals se resuelven con spotMeta en tiempo de ejecución; no se escriben a mano.
6. Ante datos dudosos el sistema NO opera (mantiene posición) y alerta. El interruptor de caída es la única excepción (a USDC).
7. Toda función nueva lleva prueba; `python -m pytest tests/ -q -m "not testnet"` debe pasar antes de cada commit.
8. Respetar 1,200 de peso/min por IP en la API de Hyperliquid; recolectores con backoff.

## Comandos
- Pruebas: `python -m pytest tests/ -q -m "not testnet"`
- Backtest de referencia: `python scripts/run_backtest.py` (sin --refit; ML apagado)
- Paper diario: `python -m atlas.cli daily` · estado: `python -m atlas.cli status`
- Réplica: `python -m atlas.cli replay --from 2025-03-01 --to 2026-10-22 --broker fake`

## Estilo
Python 3.11+, pandas, type hints, funciones pequeñas, comentarios y docs en español. Commits pequeños por fase.
```

---

## 19. Prompt inicial para Claude Code

```text
Lee CLAUDE.md y docs/PLAN_paper_trading_hyperliquid.md completos antes de hacer nada.

Vamos a construir la infraestructura de paper trading de Atlas v3 sobre Hyperliquid siguiendo ese plan.
Tú eres el orquestador y coordinas 4 terminales (T1 Datos, T2 Señal y ejecución, T3 Operación, T4 QA)
como dice la sección 17.

Empieza por la Fase 0, tú solo:
1. Inicializa la estructura de atlas/ y fija atlas/interfaces.py, el esquema SQLite y config/live_hyperliquid.yaml.
2. Instala hyperliquid-python-sdk==0.24.0 y verifica contra la API real cada fila de la sección 4
   (spotMeta de UBTC/USDC, szDecimals, comisiones, límite de 5000 velas, anclaje de la vela 1d, libro,
   pares spot de testnet, mínimo de orden). Graba las respuestas en tests/fixtures/hl/ y escribe
   docs/hl_facts_verified.md. Si algo del plan no coincide con la API, corrige el plan y dímelo.
3. Mide spot vs perp y decide la fuente de la señal con el criterio de la sección 7.4.
4. Muéstrame el resultado de F0 y el reparto de tareas para T1–T4 antes de lanzarlas.

Prioridad: el recolector de velas de 5m debe quedar corriendo en el VPS lo antes posible,
porque Hyperliquid solo guarda las últimas 5000 velas.
No cambies config/strategy.yaml ni la lógica de engine/. Nada de órdenes en mainnet.
```

---

## 20. Preguntas abiertas para Justin (con valor por defecto)

| Pregunta | Por defecto |
|---|---|
| Canal de alertas | Telegram (bot propio) + correo de healthchecks.io |
| Región del VPS de Hostinger | Verificar en F0; si es EE. UU., documentar y planear moverlo antes de cualquier operación real |
| ¿Tienes cuenta en Hyperliquid mainnet con depósito? (el faucet de testnet lo exige) | Si no, el Modo B se construye y prueba con fixtures y se activa cuando haya cuenta |
| Capital de papel | 10,000 USDC |
| Nombre del repo | `atlas-btc` privado en GitHub |
| Respaldo fuera del VPS | rsync a la laptop; Google Drive con rclone si prefieres |

---

## 21. Riesgos conocidos

- **Fuente de precio distinta al backtest** (Bitstamp → HL): se controla con la paridad de datos (7.3) y la réplica.
- **rv con ruido de libro delgado** en spot: puede bajar la exposición sin razón; por eso la decisión 7.4.
- **Pérdida de velas** si el recolector se cae más de ~17 días: respaldo Binance + alerta.
- **Cambios de la API o del SDK:** versión fijada; fixtures detectan cambios de forma; `spotMeta` se valida cada día.
- **Capa del ciclo frágil:** no se toca; se documenta su efecto en cada reporte mensual.
- **Testnet poco representativo:** solo valida plomería; los costos se miden en el Modo A con el libro de mainnet.
- No es asesoría financiera.

---

## 22. Correcciones del orquestador (2026-10-29, tras F0–F3)

Evidencia en `docs/hl_facts_verified.md`. Lo que cambió respecto al texto de arriba:

1. **Velas:** HL guarda *aprox.* 5000 velas por intervalo (se midieron 5043 de 5m y 5217 de 1m), no exactamente 5000.
   La vela de 1d spot tiene la historia completa del par (desde 2025-02-03) y coincide exactamente con la agregación de 5m.
2. **`spotMetaAndAssetCtxs`:** los contextos NO están alineados por posición con `universe`; se emparejan por `coin`.
3. **Testnet:** no existe UBTC. `@50 BTC/USDC` está roto (szDecimals 0, sin bids). `@2072 POBTC/USDC` (szDecimals 5)
   sigue el precio de BTC con un libro de un nivel → sirve solo para plomería. Además, el faucet exige depósito en mainnet.
   Con la decisión de Justin de no mover dinero, **el Modo B queda construido y probado solo con dobles** (`testnet.enabled: false`).
   Pendiente para F4: que `daily --mode testnet` resuelva el par de testnet (`testnet.spot_pair`) con el spotMeta de testnet
   para las órdenes, manteniendo la señal de mainnet.
4. **Comisiones:** la dirección cero devuelve nivel VIP1; las base (0.07%/0.04% spot) se confirmaron por la documentación y el esquema.
5. **Réplica vs `simulate`:** `simulate` trabaja en pesos (el costo no entra al denominador de la deriva); el ledger lleva
   unidades redondeadas a szDecimals con comisión en el activo recibido. Por construcción no pueden coincidir a 1e-6.
   Resultado medido 2025-03-01→2026-10-22: objetivos idénticos (dif. 0), 64/64 días de operación iguales, equity máx.
   dif. relativa 5.2e-5. Criterio nuevo: mismos días + objetivos exactos + equity < 1e-3. En la historia completa
   2018→2026 la deriva se acumula (~2% y 5 días distintos de 339) por el mismo motivo; no es un error de señal.
6. **`scripts/run_backtest.py`** necesita `data/raw/sp500_prices.parquet` (benchmarks de acciones) que no está en el repo.
   Para probar "mismos números" se usa `scripts/v3_fingerprint.py` (hash de objetivos y de la simulación + métricas).
7. **Señal en vivo:** `trend_signals` pone NaN a las primeras 200 filas por posición y las EWM dependen del inicio, así que la
   señal se calcula siempre sobre la serie cosida completa (Bitstamp desde el inicio + HL), nunca sobre una ventana.
8. **Fuente de la señal (7.4): spot UBTC/USDC** — mediana |spot/perp−1| 0.048%, rv spot/Binance 0.985, volumen ~36M/día.
9. **Plazo del recolector:** HL 5m cubre desde 2026-10-12; la semilla Bitstamp termina el 2026-10-22. Si el recolector
   arranca antes de ~2026-11-11 no hay hueco de rv. Mientras tanto, las velas de F0 están grabadas en `tests/fixtures/hl/`
   y `atlas backfill --from-fixtures` las siembra.
