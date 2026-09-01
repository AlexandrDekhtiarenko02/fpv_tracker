#!/usr/bin/env bash
# Включить или выключить запись кадров с камеры. Запускать НА МАЛИНЕ.
#
#   ./record.sh on     включить и перезапустить службу
#   ./record.sh off    выключить и перезапустить
#
# Записи ложатся в flight_logs/recordings/ и забираются с Mac обычным
# ./fetch_logs.sh вместе с логами.
set -uo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  on)  want=True ;;
  off) want=False ;;
  *)   echo "как пользоваться: ./record.sh on | off"; exit 1 ;;
esac

sed -i "s/^RECORD_FRAMES = .*/RECORD_FRAMES = ${want}          # включается вручную, когда нужен образец/" tracker.py
echo "==> RECORD_FRAMES = ${want}"
grep -n '^RECORD_FRAMES' tracker.py

echo "==> перезапускаю службу (спросит пароль)"
sudo systemctl restart tracker || { echo "не удалось перезапустить"; exit 1; }
sleep 2
./status.sh 2>/dev/null | tail -4

if [ "$want" = "True" ]; then
    echo
    echo "Запись включена. Захватывай цель — запись начинается вместе со"
    echo "слежением и кончается вместе с ним. Предел одной записи 25 с."
    echo "Сняв образцы, ОБЯЗАТЕЛЬНО выключи: ./record.sh off"
    echo "Потом с Mac:  ./fetch_logs.sh"
else
    echo
    echo "Запись выключена."
fi
