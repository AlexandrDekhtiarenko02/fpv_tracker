#!/usr/bin/env bash
# Забрать полётные логи с малины к себе (в папку flight_logs/ рядом со скриптом).
#
#   ./fetch_logs.sh
#   PI=192.168.1.50 ./fetch_logs.sh
set -euo pipefail

PI="${PI:-Monolith.local}"
PI_USER="${PI_USER:-alex243}"
PI_DIR="${PI_DIR:-~/fpv_tracker}"

mkdir -p flight_logs
echo "==> Забираю логи с ${PI_USER}@${PI}:${PI_DIR}/flight_logs/"
rsync -avz "${PI_USER}@${PI}:${PI_DIR}/flight_logs/" ./flight_logs/
echo "==> Готово. Файлы в ./flight_logs/"
ls -lt flight_logs | head
