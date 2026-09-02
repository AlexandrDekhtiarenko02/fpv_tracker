#!/usr/bin/env bash
# Включить или выключить запись кадров с камеры. Запускать НА МАЛИНЕ.
#
#   ./record.sh on     обычная запись, 320x240
#   ./record.sh hi     запись в ДВОЙНОМ разрешении (ведём по-прежнему по
#                      320x240, но в файл идёт 640x480 — чтобы сравнить оба
#                      разрешения на земле)
#   ./record.sh off    выключить
#
# Настройки пишутся в local_settings.py, а НЕ в tracker.py. Раньше правился
# сам tracker.py, и обновление падало с «local changes would be overwritten»:
# правка настроек и правка программы — разные вещи.
#
# Записи ложатся в flight_logs/recordings/ и забираются с Mac обычным
# ./fetch_logs.sh вместе с логами.
set -uo pipefail
cd "$(dirname "$0")"
CFG=local_settings.py

case "${1:-}" in
  on)  frames=True;  hires=False ;;
  hi)  frames=True;  hires=True  ;;
  off) frames=False; hires=False ;;
  *)   echo "как пользоваться: ./record.sh on | hi | off"
       echo "   on  — обычная запись 320x240"
       echo "   hi  — двойное разрешение в файл, ведём по-прежнему"
       echo "   off — выключить"
       exit 1 ;;
esac

cat > "$CFG" <<CFGEOF
# Настройки ЭТОГО борта. Файл не в репозитории и обновлением не затирается.
# Создан ./record.sh $(date '+%Y-%m-%d %H:%M').
RECORD_FRAMES = ${frames}
RECORD_HIRES = ${hires}
CFGEOF

echo "==> $CFG:"
sed 's/^/    /' "$CFG"

echo "==> перезапускаю службу (спросит пароль)"
sudo systemctl restart tracker || { echo "не удалось перезапустить"; exit 1; }
sleep 2
./status.sh 2>/dev/null | tail -4

if [ "$frames" = "True" ]; then
    echo
    echo "Запись включена${hires:+}."
    [ "$hires" = "True" ] && echo "Разрешение записи ДВОЙНОЕ; слежение идёт как обычно."
    echo "Захватывай цель — запись начинается вместе со слежением и кончается"
    echo "вместе с ним. Предел одной записи 25 с."
    echo "Сняв образцы, ОБЯЗАТЕЛЬНО выключи: ./record.sh off"
    echo "Потом с Mac:  ./fetch_logs.sh"
else
    echo
    echo "Запись выключена."
fi
