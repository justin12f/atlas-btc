"""Utilidades compartidas de pruebas: fixtures de Hyperliquid grabados en la Fase 0."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FX = ROOT / "tests" / "fixtures" / "hl"


def load_fx(name: str):
    return json.load(open(FX / f"{name}.json"))


@pytest.fixture
def fx():
    return load_fx
