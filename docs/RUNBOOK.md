# RUNBOOK de Atlas (operación en el VPS)

Todos los comandos como el usuario `atlas`:

```bash
sudo -u atlas env $(sudo cat /etc/atlas/atlas.env | xargs) /opt/atlas/.venv/bin/python -m atlas.cli <comando>
# alias sugerido en ~/.bashrc de tu usuario:
alias atlas='sudo -u atlas env $(sudo cat /etc/atlas/atlas.env | xargs) /opt/atlas/.venv/bin/python -m atlas.cli'
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
| Eventos en la base | `sqlite3 /var/lib/atlas/data/live/atlas.db "select ts,level,kind,message from events order by id desc limit 20"` |

## Alertas: síntoma → causa probable → acción

| Síntoma | Causa probable | Acción |
|---|---|---|
| healthchecks.io avisa "no llegó el ping" | La corrida diaria no terminó: VPS caído, timer apagado, proceso colgado | `systemctl status atlas-daily`; `journalctl -u atlas-daily`. Si el VPS se reinició, `Persistent=true` la corre al arrancar. Correr a mano `atlas daily --mode paper` (idempotente: si ya hay ledger del día, no hace nada). |
| Telegram: `ERROR en daily ...` | Excepción (red, API, bug) | Leer el mensaje y `journalctl`. Volver a correr `atlas daily`: reutiliza fills guardados y no duplica. Si se repite, abrir issue; no editar código en el VPS. |
| `BLOQUEO: datos viejos (X min > 20)` | El recolector no corrió o HL no respondió | `journalctl -u atlas-collect`; correr `atlas collect`; luego `atlas daily`. Si HL está caído, esperar: el día queda sin operar (posición se mantiene). |
| `BLOQUEO: barra con banderas: n_5m=…/gap…/jump_unconfirmed` | Faltan velas de 5m del día o un salto sin confirmar en el perp | `atlas backfill` (rellena desde HL); si faltan definitivamente, `atlas backfill --binance-days 3` (respaldo de rv). Revisar `atlas status`. Si sigue, el día no opera. |
| `BLOQUEO: divergencia spot_vs_perp=…` | Libro spot de UBTC raro (desanclaje de UBTC, libro vacío) | **No forzar.** Revisar en la UI de HL el precio UBTC vs BTC. Si UBTC está desanclado, es un riesgo de custodia (Unit): documentar y esperar. |
| `BLOQUEO: spotMeta cambió (índice o szDecimals)` | HL cambió el par o su precisión | Revisar `spotMeta` (índice de UBTC/USDC, szDecimals). Si el cambio es legítimo: `atlas ack-market --pair UBTC/USDC`. |
| `BLOQUEO: señal: …` | Falta rv/cierre del día o serie corta | `atlas backfill`; revisar `atlas status`. |
| `caída -15%` / `-25%` | Drawdown normal de la estrategia | Solo informativo. No hacer nada. |
| `INTERRUPTOR: caída … < -36%` | Se activó el interruptor: todo a USDC y pausa | La pausa es **manual** en vivo. Revisar qué pasó (mercado vs bug de datos/ledger). Para reanudar: `atlas resume --mode paper --note "qué revisé y por qué reanudo"` (reinicia el máximo a la equity actual). |
| `otro proceso 'daily-paper' está corriendo` | Dos corridas a la vez (flock) | Normal si coincidió con una corrida a mano. Nada que hacer. |
| Disco lleno | Respaldos o logs | `du -sh /var/lib/atlas/*`; los respaldos >30 días se borran solos. `journalctl --vacuum-time=30d`. |

## Simulacros de F5 (antes del día 1)

1. **Matar a mitad de ejecución**: `systemctl start atlas-daily` y, durante la espera maker (los primeros 30 min),
   `systemctl kill atlas-daily`. Volver a correr: debe recuperar la orden (mismo día, cloid siguiente) sin
   duplicar fills. Verificar con `sqlite3 ... "select * from orders order by created_at desc limit 3"`.
2. **Reiniciar el VPS** a las 00:03 UTC: al volver, `Persistent=true` corre la diaria; `atlas status` debe mostrar
   un solo ledger del día.
3. **Sin red** 2 h: el recolector registra errores; el siguiente `collect` rellena (ventana con traslape).

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
