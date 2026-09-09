#!/usr/bin/env bash
# Обновить борт безопасно: забрать код, дождаться записи на карту, проверить.
#
# ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ, а не просто `git pull`. Карта монтирована ext4 с
# отложенной записью: имя файла попадает на диск сразу, содержимое — когда
# ядро соберётся. Если питание пропадёт в этом окне, останутся файлы нулевого
# размера с целыми именами. За 9 сентября 2026 это случилось трижды: tracker.py
# обнулялся, служба уходила в цикл перезапусков (пустой файл Python выполняет
# без ошибок), а git падал с «object file is empty».
#
# sync здесь и есть лекарство: он не возвращает управление, пока всё
# записанное не окажется на карте. Секунда ожидания против часа разбора.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> Забираю код"
if ! git fetch origin; then
    echo "    НЕ ВЫШЛО. Сеть недоступна либо репозиторий повреждён."
    echo "    Если повреждён — ./pochinit.sh"
    exit 1
fi
VETKA="$(git rev-parse --abbrev-ref HEAD)"
if ! git merge --ff-only "origin/${VETKA}"; then
    echo "    НЕ ВЫШЛО перемотать ветку ${VETKA}."
    echo "    Либо на борту есть свои коммиты, либо репозиторий повреждён."
    exit 1
fi

echo "==> Дожидаюсь записи на карту"
sync
echo "    записано"

echo "==> Проверяю, что файл цел"
if [ ! -s tracker.py ]; then
    echo "    tracker.py ПУСТОЙ. Это та самая порча: ./pochinit.sh"
    exit 1
fi
if ! python3 -c "import ast,io;ast.parse(io.open('tracker.py',encoding='utf-8').read())"; then
    echo "    tracker.py не разбирается. Не перезапускай службу: ./pochinit.sh"
    exit 1
fi
echo "    цел, $(wc -c < tracker.py) байт"

echo
echo "==> Готово: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
echo
echo "Перезапустить службу (это под sudo, запускай сам):"
echo "    sudo systemctl restart tracker && sleep 8 && ./status.sh"
