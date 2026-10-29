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
