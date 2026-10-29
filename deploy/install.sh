#!/usr/bin/env bash
# Instalación de Atlas en el VPS (sección 12). Correr como root:  sudo bash deploy/install.sh <url-del-repo>
# Idempotente: se puede volver a correr para actualizar.
set -euo pipefail

REPO_URL="${1:-}"
APP=/opt/atlas
DATA=/var/lib/atlas
ETC=/etc/atlas

need() { command -v "$1" >/dev/null || { echo "falta $1"; exit 1; }; }
need python3; need git; need systemctl
python3 -c 'import sys; assert sys.version_info >= (3, 11), "se requiere Python >= 3.11"'
PYV="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 -c 'import ensurepip' 2>/dev/null || { apt-get update -q && apt-get install -y -q "python$PYV-venv"; }
command -v sqlite3 >/dev/null || { apt-get update -q && apt-get install -y -q sqlite3; }
# Reloj: basta con que esté sincronizado (timesyncd o chrony)
timedatectl show -p NTPSynchronized --value | grep -qx yes || echo "AVISO: reloj no sincronizado (timedatectl)"

# Usuario de sistema sin shell de login ni sudo
id atlas >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin atlas
install -d -o atlas -g atlas -m 750 "$DATA" "$DATA/data/live" "$DATA/backups" "$DATA/reports"
install -d -o root -g atlas -m 750 "$ETC"

if [ ! -d "$APP/.git" ]; then
  [ -n "$REPO_URL" ] || { echo "uso: install.sh <url-del-repo> (primera vez)"; exit 1; }
  git clone "$REPO_URL" "$APP"
else
  git -C "$APP" pull --ff-only
fi
chown -R atlas:atlas "$APP"

sudo -u atlas python3 -m venv "$APP/.venv"
sudo -u atlas "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u atlas "$APP/.venv/bin/pip" install -q -r "$APP/requirements-live.txt"

# La semilla histórica (data/processed/btc_daily.parquet) viene en el repo.
# Pruebas antes de activar timers (regla 7)
TMPD="$(sudo -u atlas mktemp -d)"
sudo -u atlas env ATLAS_DATA_DIR="$TMPD" bash -c "cd $APP && .venv/bin/python -m pytest tests/ -q -m 'not testnet'"

if [ ! -f "$ETC/atlas.env" ]; then
  install -o atlas -g atlas -m 600 "$APP/deploy/atlas.env.example" "$ETC/atlas.env"
  echo ">> edita $ETC/atlas.env (Telegram / healthchecks)"
fi
chmod 600 "$ETC/atlas.env"; chown atlas:atlas "$ETC/atlas.env"

install -m 644 "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
install -m 755 "$APP/deploy/backup.sh" /usr/local/bin/atlas-backup
systemctl daemon-reload
for t in atlas-collect atlas-daily atlas-weekly atlas-backup; do systemctl enable --now "$t.timer"; done
systemctl list-timers 'atlas-*'
echo ">> listo. Primer paso: sudo -u atlas env ATLAS_DATA_DIR=$DATA $APP/.venv/bin/python -m atlas.cli backfill --from-fixtures"
