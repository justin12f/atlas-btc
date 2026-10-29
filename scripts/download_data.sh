#!/usr/bin/env bash
# Descarga los datos publicos usados en el backtest (~400 MB) a data/raw/
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw && cd data/raw
GH=https://raw.githubusercontent.com
curl -L -o btc_bitstamp_hist.csv.gz   $GH/ff137/bitstamp-btcusd-minute-data/main/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz
curl -L -o btc_bitstamp_latest.csv    $GH/ff137/bitstamp-btcusd-minute-data/main/data/updates/btcusd_bitstamp_1min_latest.csv
for a in btc usdc usdt; do curl -L -o cm_$a.csv $GH/coinmetrics/data/master/csv/$a.csv; done
curl -L -o sp500_prices.parquet  https://github.com/Johnbrick123/sp500-data/releases/download/data/prices.parquet
curl -L -o sp500_members.parquet https://github.com/Johnbrick123/sp500-data/releases/download/data/members.parquet
echo "Listo. Ahora: python -m engine.data"
