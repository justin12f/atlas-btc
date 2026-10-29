# Para Justin — qué te toca a ti (2026-10-29)

Todo lo que podía hacer sin ti está hecho y en git (4 commits en `main`, 114 pruebas en verde). Falta lo que
requiere tus accesos, tu dinero o tus decisiones. Ordenado por urgencia.

## 🔴 Urgente (esta semana): poner el recolector en el VPS

Hyperliquid solo guarda ~17 días de velas de 5m. Ya grabé todo lo que había (2026-10-11 → 2026-10-29 05:15) en
`tests/fixtures/hl/`, así que **el plazo real es ~2026-11-14**: si el recolector no corre antes, se pierde rv de
algunos días y habrá que usar el respaldo de Binance.

1. **Crear el repo privado en GitHub** (`atlas-btc`) y subirlo desde la laptop:
   ```bash
   cd ~/Downloads/btc_quant_v2
   git remote add origin git@github.com:<tu-usuario>/atlas-btc.git
   git push -u origin main
   ```
   (Yo no hago push. El conector de GitHub falló al conectarse en esta sesión.)
2. **Entrar al VPS de Hostinger** y revisar: región (¿EE. UU.?), versión de Python (`python3 --version`, se necesita ≥ 3.11).
   Si es menor de 3.11: `sudo apt install python3.12 python3.12-venv` (o dime y adapto el instalador).
3. **Instalar** (como root en el VPS):
   ```bash
   git clone <url> /tmp/atlas && sudo bash /tmp/atlas/deploy/install.sh <url>
   sudo -u atlas env ATLAS_DATA_DIR=/var/lib/atlas /opt/atlas/.venv/bin/python -m atlas.cli backfill --from-fixtures
   sudo -u atlas env ATLAS_DATA_DIR=/var/lib/atlas /opt/atlas/.venv/bin/python -m atlas.cli backfill
   ```
   El instalador corre las pruebas antes de activar los timers. Para clonar un repo privado en el VPS necesitas
   una deploy key de solo lectura (GitHub → Settings → Deploy keys).
4. Verificar: `systemctl list-timers 'atlas-*'` y, una hora después, `atlas status` (ver docs/RUNBOOK.md).

> Ojo: el timer diario también arranca el papel (Modo A) desde el primer día. Si prefieres que el VPS solo
> recolecte hasta aprobar el pre-registro, después de instalar corre `sudo systemctl disable --now atlas-daily.timer`.

## 🟠 Decisiones que necesito de ti

| # | Decisión | Mi recomendación / valor por defecto |
|---|---|---|
| 1 | **Aprobar `config/preregistro_paper.yaml`** (criterios de la sección 15). Al aprobar: `estado: aprobado`, tu nombre, fecha, `strategy_sha256` y commit. | Aprobar tal cual. Es lo que dice el plan. |
| 2 | **Día 1 del periodo de papel** | Tras 7 días desatendidos del VPS sin fallas (F5). Si instalas esta semana → ~2026-11-07. |
| 3 | **Modo B (testnet)**: sigue apagado porque decidiste no mover dinero (el faucet exige depósito en mainnet, y testnet no tiene UBTC). | Dejarlo apagado. No aporta medición de costos; solo plomería. Reconsiderar a los 3 meses. |
| 4 | **Respaldo fuera del VPS** | rsync a la laptop no sirve si la laptop está apagada → **Google Drive con rclone** (`ATLAS_BACKUP_REMOTE=rclone:gdrive:atlas`). |
| 5 | **Región del VPS** | Si está en EE. UU.: no importa para papel (solo lee datos públicos). Importa antes de cualquier operación real. |
| 6 | **Capital de papel** | 10,000 USDC (ya configurado). |
| 7 | ¿Quieres página HTML de reportes además de markdown/CSV? | No por ahora. |

## 🟡 Cuentas que tienes que crear tú (10 min, opcionales pero recomendadas)

- **Bot de Telegram**: hablar con @BotFather → `/newbot` → token. Mandarle un mensaje al bot y sacar tu chat id
  (`https://api.telegram.org/bot<TOKEN>/getUpdates`). Poner `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `/etc/atlas/atlas.env`.
- **healthchecks.io** (gratis): crear un check con periodo 1 día y gracia 2 h; poner su URL en `HEALTHCHECKS_URL`.
  Te avisa por correo si la corrida diaria no termina.
- **Nunca** pegues estos valores en el repo, en chats con Claude ni en issues. Solo en `/etc/atlas/atlas.env`.

## 🟢 Lo que encontré y debes saber (F0)

- Par de la señal: **spot UBTC/USDC (`@142`)**, szDecimals 5. Se resuelve con spotMeta cada día; si cambia, Atlas se
  bloquea hasta que corras `atlas ack-market`.
- Spot vs perp: diferencia mediana 0.048%; rv spot/Binance 0.985; spread 0.12 bps; 10k USDC mueven ~0.7 bps.
  → los costos reales deberían quedar muy por debajo del 0.13% que asume el backtest.
- Señal actual (2026-10-22 → 10-28): **objetivo 75% BTC** (tendencia 0.75, ciclo ×1, vol ~32%).
- Réplica 2025-03-01→2026-10-22: los mismos 64 días de operación que el backtest, objetivos idénticos; la equity
  difiere 5e-5 (unidades vs pesos). El plan decía 1e-6, que es imposible por construcción → corregido en §22 del plan.
- `scripts/run_backtest.py` no corre: falta `data/raw/sp500_prices.parquet` (benchmarks). La estrategia en sí se
  verifica con `scripts/v3_fingerprint.py` (huella idéntica antes y después del único refactor de `engine/`).
- Detalle completo: `docs/hl_facts_verified.md` y §22 de `docs/PLAN_paper_trading_hyperliquid.md`.

## Cosas que NO hice (y por qué)

- **Push / VPS**: necesitan tus accesos.
- **Modo B real**: sin fondos en testnet. El código existe (`TestnetBroker`) y está probado con dobles; falta que
  `daily --mode testnet` resuelva el par de testnet para las órdenes (anotado en §22 punto 3).
- **F7 (funding carry)**: es investigación y va después del inicio del papel; además su variante F1 implica un corto
  en perpetuo, que choca con "sin cortos" y requiere tu aprobación explícita.
- No toqué `config/strategy.yaml` ni la lógica de `engine/` (solo el refactor aprobado `should_trade`, sin cambio de resultados).

## Reparto T1–T4 (si quieres paralelizar lo que queda)

Casi todo F1–F3/F5/F6 ya está hecho en una sola rama. Lo que queda es poco y secuencial, así que **recomiendo no
abrir 4 terminales**: con una basta. Si aun así quieres repartir:

| Terminal | Pendiente |
|---|---|
| T1 Datos | Reporte de paridad HL vs Bitstamp/Binance de los primeros 7 días del VPS (criterio F1). |
| T2 Ejecución | F4: par de testnet en `daily --mode testnet` + pruebas `@pytest.mark.testnet` (solo si activas el Modo B). |
| T3 Operación | Acompañarte en la instalación del VPS y los simulacros de F5 (RUNBOOK). |
| T4 Reportes | Reporte mensual y, opcional, página HTML. |
