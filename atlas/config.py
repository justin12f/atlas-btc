"""Carga de config/live_hyperliquid.yaml, hash de la estrategia y secretos por variables de entorno."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LIVE_CONFIG = ROOT / "config" / "live_hyperliquid.yaml"
STRATEGY_CONFIG = ROOT / "config" / "strategy.yaml"

# Únicos secretos que se leen; nunca se escriben a disco ni a logs.
SECRET_VARS = ("HL_TESTNET_ACCOUNT", "HL_TESTNET_AGENT_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
               "HEALTHCHECKS_URL")


def load_live_config(path: str | Path | None = None) -> dict:
    c = yaml.safe_load(open(path or LIVE_CONFIG))
    if c.get("mode") not in ("paper", "testnet", "both"):
        raise ValueError(f"mode inválido: {c.get('mode')!r} (live no existe en este plan)")
    return c


def data_dir() -> Path:
    """Raíz de datos: ATLAS_DATA_DIR en el VPS, el repo en la laptop."""
    return Path(os.environ.get("ATLAS_DATA_DIR", ROOT))


def db_path(c: dict) -> Path:
    p = Path(c["data"]["db"])
    return p if p.is_absolute() else data_dir() / p


def strategy_hash(path: str | Path | None = None) -> str:
    """sha256 de config/strategy.yaml: se guarda con cada señal para detectar cambios."""
    return hashlib.sha256(Path(path or STRATEGY_CONFIG).read_bytes()).hexdigest()


def code_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "atlas", "engine", "config"], cwd=ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (sha or "nogit") + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def secret(name: str) -> str | None:
    if name not in SECRET_VARS:
        raise KeyError(f"{name} no es un secreto conocido")
    v = os.environ.get(name)
    return v or None
