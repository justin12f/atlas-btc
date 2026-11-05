# RUNBOOK de Atlas (operación en el VPS)

Todos los comandos como el usuario `atlas`:

```bash
sudo -u atlas env $(sudo grep -v "^\s*#" /etc/atlas/atlas.env | grep = | xargs) /opt/atlas/.venv/bin/python -m atlas.cli <comando>
# alias sugerido en ~/.bashrc de tu usuario:
alias atlas='sudo -u atlas env $(sudo grep -v "^\s*#" /etc/atlas/atlas.env | grep = | xargs) /opt/atlas/.venv/bin/python -m atlas.cli'
```

Regla general: **ante datos dudosos Atlas no opera y mantiene la posición** (regla 6). Un día sin operar casi
nunca cuesta nada; una operación con datos malos sí. No hay prisa.

## Comprobaciones rápidas

| Qué | Comando |
|---|---|
| Estado general (velas, ledger por modo, últimos eventos) | `atlas status` |
| Timers | `systemctl list-timers 'atlas-*'` |
| Log de la última corrida diaria | `journalctl -u atlas-daily -n 200 --no-pager` |
| Log del recolector | `journalctl -u atlas-collect -n 100 --no-pager` |
| Reporte del día | `ls /var/lib/atlas/reports/daily/` |
| Sombra G1 (no opera; se escribe tras cada `daily`) | `atlas status` (bloque `shadow_g1`); rellenar faltantes: `atlas shadow-g1` |
| Eventos en la base | `sqlite3 /var/lib/atlas/data/live/atlas.db "select ts,level,kind,message from events order by id desc limit 20"` |

## Alertas: síntoma → causa probable → acción

| Síntoma | Causa probable | Acción |
|---|---|---|
| healthchecks.io avisa "no llegó el ping" | La corrida diaria no terminó: VPS caído, timer apagado, proceso colgado | `systemctl status atlas-daily`; `journalctl -u atlas-daily`. Si el VPS se reinició, `Persistent=true` la corre al arrancar. Correr a mano `atlas daily --mode paper` (idempotente: si ya hay ledger del día, no hace nada). |
| Telegram: `ERROR en daily ...` | Excepción (red, API, bug) | Leer el mensaje y `journalctl`. Volver a correr `atlas daily`: reutiliza fills guardados y no duplica. Si se repite, abrir issue; no editar código en el VPS. |
| `RECOLECTOR: 2 corridas seguidas con error` | Sin red o HL caído (cada fallo queda como evento `collect`) | Revisar red/estado de HL. No hay que hacer nada más: al volver, el siguiente `collect` rellena con el traslape y llega "Recolector recuperado". |
| `BLOQUEO: datos viejos (X min > 20)` | El recolector no corrió o HL no respondió | `journalctl -u atlas-collect`; correr `atlas collect`; luego `atlas daily` (el modo seguro no cierra el día, así que el reintento sí opera). Si HL está caído, esperar: el día queda sin operar (posición se mantiene). |
| `BLOQUEO: barra con banderas: n_5m=…/gap…/jump_unconfirmed` | Faltan velas de 5m del día o un salto sin confirmar en el perp | `atlas backfill` (rellena desde HL); si faltan definitivamente, `atlas backfill --binance-days 3` (respaldo de rv). Revisar `atlas status`. Si sigue, el día no opera. |
| `BLOQUEO: divergencia spot_vs_perp=…` | Libro spot de UBTC raro (desanclaje de UBTC, libro vacío) | **No forzar.** Revisar en la UI de HL el precio UBTC vs BTC. Si UBTC está desanclado, es un riesgo de custodia (Unit): documentar y esperar. |
| `BLOQUEO: spotMeta cambió (índice o szDecimals)` | HL cambió el par o su precisión | Revisar `spotMeta` (índice de UBTC/USDC, szDecimals). Si el cambio es legítimo: `atlas ack-market --pair UBTC/USDC`. |
| `BLOQUEO: señal: …` | Falta rv/cierre del día o serie corta | `atlas backfill`; revisar `atlas status`. |
| `caída -15%` / `-25%` | Drawdown normal de la estrategia | Solo informativo. No hacer nada. |
| `INTERRUPTOR: caída … < -36%` | Se activó el interruptor: todo a USDC y pausa | La pausa es **manual** en vivo. Revisar qué pasó (mercado vs bug de datos/ledger). Para reanudar: `atlas resume --mode paper --note "qué revisé y por qué reanudo"` (reinicia el máximo a la equity actual). |
| `otro proceso 'daily-paper' está corriendo` | Dos corridas a la vez (flock) | Normal si coincidió con una corrida a mano. Nada que hacer. |
| Disco lleno | Respaldos o logs | `du -sh /var/lib/atlas/*`; los respaldos >30 días se borran solos. `journalctl --vacuum-time=30d`. |

## Simulacros de F5

Hechos el 2026-11-05 sobre copias de la base (`/var/lib/atlas-drill*`), sin tocar el ledger real:

| Simulacro | Resultado |
|---|---|
| SIGKILL de `daily` a los 10 s (orden en `working`, sin fill ni ledger) | Reintento: cloid siguiente, **un solo fill**, día cerrado; el tercer intento da `already_done`. Restos marcados `abandoned` / `crashed`. |
| Datos viejos → modo seguro | Antes cerraba el día y bloqueaba el reintento (corregido: el modo seguro ya no escribe ledger). |
| Sin red (HTTPS del usuario `atlas` bloqueado) con 2 h de velas borradas | Antes: traceback mudo (corregido: evento `collect` + alerta a la 2.ª falla). Con red: rellenó las 2 h completas. |
| Reinicio del VPS a las 00:03 UTC | **Pendiente**: el VPS es compartido con clinica y leoncaviec; requiere tu permiso. |

Para repetirlos: copiar la base con `sqlite3 atlas.db ".backup /var/lib/atlas-drill/data/live/atlas.db"` y correr
los comandos con `ATLAS_DATA_DIR=/var/lib/atlas-drill`. Nunca simular sobre `/var/lib/atlas`.

## Actualizar el código

```bash
cd /opt/atlas && sudo -u atlas git pull --ff-only
sudo -u atlas .venv/bin/pip install -r requirements-live.txt
sudo -u atlas env ATLAS_DATA_DIR=$(sudo -u atlas mktemp -d) .venv/bin/python -m pytest tests/ -q -m "not testnet"
sudo systemctl daemon-reload   # solo si cambiaron las unidades (vuelve a correr deploy/install.sh)
```

Nunca editar código en el VPS. Nunca tocar `config/strategy.yaml`.

## Respaldos

- Diario 02:00 UTC en `/var/lib/atlas/backups/atlas-AAAA-MM-DD.db.gz` (30 días).
- Copia fuera: `ATLAS_BACKUP_REMOTE=rclone:gdrive:atlas` o `rsync:usuario@laptop:/ruta/` en `/etc/atlas/atlas.env`.
- Restaurar: `systemctl stop atlas-*.timer; gunzip -c respaldo.db.gz > /var/lib/atlas/data/live/atlas.db; chown atlas: ...; systemctl start atlas-*.timer`.

## Testnet (Modo B) — desactivado

Para activarlo hace falta: cuenta de mainnet con depósito (requisito del faucet), una dirección dedicada, un agente
(API wallet) aprobado **desde la laptop**, y en `/etc/atlas/atlas.env` solo `HL_TESTNET_ACCOUNT` y
`HL_TESTNET_AGENT_KEY`. Luego `testnet.enabled: true` en `config/live_hyperliquid.yaml` (commit, no edición en el VPS)
y un timer adicional `atlas daily --mode testnet`. Ojo: testnet no tiene UBTC; `POBTC/USDC` solo prueba plomería.
