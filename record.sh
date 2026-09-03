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

# Меняем только нужный параметр. Полная перезапись здесь удаляла
# OBSERVE_ONLY, координаты цели и остальные настройки конкретного борта.
update_setting() {
  key=$1
  value=$2
  tmp="${CFG}.tmp.$$"

  if [ -f "$CFG" ]; then
    awk -v key="$key" -v value="$value" '
      BEGIN { found = 0 }
      $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
        if (!found) print key " = " value
        found = 1
        next
      }
      { print }
      END { if (!found) print key " = " value }
    ' "$CFG" > "$tmp" || return 1
  else
    {
      echo "# Настройки ЭТОГО борта. Файл не в репозитории."
      echo "# Создан ./record.sh $(date '+%Y-%m-%d %H:%M')."
      echo "$key = $value"
    } > "$tmp" || return 1
  fi
  mv "$tmp" "$CFG"
}

update_setting RECORD_FRAMES "$frames"
update_setting RECORD_HIRES "$hires"

# Запись образцов всегда означает ручной заход пилота. Включаем наблюдение
# сами, чтобы старая или неполная local_settings.py не отдала управление
# трекеру при захвате цели. Команда off этот флаг намеренно не снимает.
if [ "$frames" = "True" ]; then
  update_setting OBSERVE_ONLY True
fi

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
    echo "Режим OBSERVE_ONLY не изменён."
fi
