"""
Exporta los datos del tablero "Atlas vs lo esperado" tras cada corrida diaria (solo lectura del ledger).

Escribe <data_dir>/dashboard/paper_daily.csv y, si ATLAS_DASH_REMOTE está definido (p. ej.
"gdrive:Atlas/atlas_paper_daily.csv"), lo sube con `rclone copyto`, que actualiza el mismo archivo en Drive.
El tablero lee ese archivo con la conexión de Google Drive de quien lo abre. Fallas -> evento + alerta,
nunca afectan la corrida del papel. La configuración de rclone (token) vive en el VPS, nunca en el repo.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from atlas.variants import ledgers_for_dashboard, paper_daily

REMOTE_ENV = "ATLAS_DASH_REMOTE"


def write_csv(store, out: Path) -> Path:
    paper, g1 = ledgers_for_dashboard(store)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    paper_daily(paper, g1).to_csv(tmp, index=False)
    tmp.replace(out)
    return out


def upload(path: Path, remote: str, run=subprocess.run) -> None:
    r = run(["rclone", "copyto", str(path), remote], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"rclone {r.returncode}: {(r.stderr or '').strip()[-300:]}")


def export_dashboard(store, data_dir: Path, alerter=None, run=subprocess.run) -> str:
    """Devuelve 'subido', 'local' (sin remoto configurado) o 'error'."""
    try:
        path = write_csv(store, Path(data_dir) / "dashboard" / "paper_daily.csv")
        remote = os.environ.get(REMOTE_ENV)
        if not remote:
            return "local"
        upload(path, remote, run)
        return "subido"
    except Exception as e:
        store.log_event("warn", "dashboard", f"{type(e).__name__}: {e}")
        if alerter:
            alerter.send(f"TABLERO: no se pudo actualizar ({type(e).__name__}: {e})", "warn")
        return "error"
