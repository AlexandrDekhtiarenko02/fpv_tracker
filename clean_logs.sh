#!/usr/bin/env bash
# Почистить полётные логи, СОХРАНИВ записи кадров.
#
#   ./clean_logs.sh          показать, что будет удалено
#   ./clean_logs.sh --yes    удалить
#
# Записи кадров (recordings/) НЕ ТРОГАЮТСЯ: это наш испытательный набор, по
# нему проверяются все правки. Разборы (rendered/) тоже остаются.
set -uo pipefail
cd "$(dirname "$0")"
D=flight_logs
[ -d "$D" ] || { echo "нет каталога $D"; exit 0; }

echo "==> Будет удалено:"
# -maxdepth обязан идти ПЕРЕД условиями: это глобальная опция, и GNU find
# на малине ругается, если поставить её после -name.
n_csv=$(find "$D" -maxdepth 1 -name 'flight_*.csv' | wc -l | tr -d ' ')
n_ev=$(find "$D" -maxdepth 1 \( -name 'flight_*.log' -o -name 'flight_*.txt' \) | wc -l | tr -d ' ')
sz=$(du -sh "$D" 2>/dev/null | cut -f1)
echo "    полётных логов: ${n_csv} csv, ${n_ev} прочих"
if [ -d "$D/acq_debug" ]; then
    echo "    снимков захвата: $(ls "$D/acq_debug" | wc -l | tr -d ' ') файлов"
fi
echo "==> Будет СОХРАНЕНО:"
if [ -d "$D/recordings" ]; then
    echo "    записей кадров: $(ls "$D"/recordings/*.gray 2>/dev/null | wc -l | tr -d ' ') штук, $(du -sh "$D/recordings" 2>/dev/null | cut -f1)"
fi
[ -d "$D/rendered" ] && echo "    разборов: $(ls "$D/rendered" | wc -l | tr -d ' ')"
echo "    сейчас всего: ${sz}"

if [ "${1:-}" != "--yes" ]; then
    echo
    echo "Ничего не удалено. Чтобы удалить: ./clean_logs.sh --yes"
    exit 0
fi

find "$D" -maxdepth 1 -name 'flight_*' -delete
rm -rf "$D/acq_debug"
echo "==> Готово. Осталось: $(du -sh "$D" 2>/dev/null | cut -f1)"
