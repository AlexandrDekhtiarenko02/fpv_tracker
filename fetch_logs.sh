#!/usr/bin/env bash
# Забрать полётные логи с малины к себе.
#
#   ./fetch_logs.sh                      в flight_logs/ рядом со скриптом
#   ./fetch_logs.sh ~/Desktop/Logs       в указанную папку
#   PI=192.168.1.50 ./fetch_logs.sh      если имя борта не резолвится
#
# Запускать НА МАКЕ, из любого места: скрипт сам переходит к себе в каталог,
# поэтому путь до него набирать не нужно.
set -euo pipefail

PI="${PI:-Monolith.local}"
PI_USER="${PI_USER:-alex243}"
PI_DIR="${PI_DIR:-~/fpv_tracker}"

# Каталог назначения. Без него — flight_logs/ рядом со скриптом, как было.
cd "$(dirname "$0")"
KUDA="${1:-flight_logs}"
mkdir -p "$KUDA"
echo "==> Забираю логи с ${PI_USER}@${PI}:${PI_DIR}/flight_logs/"
echo "    кладу в $(cd "$KUDA" && pwd)"
if ! rsync -avz "${PI_USER}@${PI}:${PI_DIR}/flight_logs/" "${KUDA}/"; then
    echo
    echo "    НЕ ВЫШЛО. Если дело в имени борта — найди адрес и повтори:"
    echo "        ./find_pi.sh"
    echo "        PI=<адрес> ./fetch_logs.sh ${KUDA}"
    exit 1
fi
echo "==> Готово: $(cd "$KUDA" && pwd)"
ls -lt "$KUDA" | head
