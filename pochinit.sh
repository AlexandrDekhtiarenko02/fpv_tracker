#!/usr/bin/env bash
# Починить репозиторий после пропажи питания.
#
# Признак: git пишет «object file ... is empty», либо tracker.py нулевого
# размера, либо служба крутится в цикле перезапусков без единой ошибки в
# журнале (пустой файл Python выполняет молча и сразу выходит).
#
# Лечится это всегда одинаково: выбросить пустые объекты и взять всё заново с
# origin. Настройки борта (local_settings.py) и логи лежат вне git и не
# страдают — поэтому сброс безопасен.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> Что повреждено"
PUSTYE_OBJ=$(find .git/objects -type f -size 0 2>/dev/null | wc -l)
echo "    пустых объектов git: ${PUSTYE_OBJ}"
find . -maxdepth 2 -type f -size 0 -not -path "./.git/*" -not -path "./flight_logs/*" \
    2>/dev/null | sed 's/^/    пустой файл: /'

echo "==> Выбрасываю пустые объекты"
find .git/objects -type f -size 0 -delete 2>/dev/null
echo "==> Беру код заново с origin"
VETKA="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo control-tuning)"
git fetch origin || { echo "    сеть недоступна — без неё не починить"; exit 1; }
git reset --hard "origin/${VETKA}" || exit 1
sync

echo "==> Проверка"
git fsck --no-progress 2>&1 | head -5
if [ ! -s tracker.py ]; then
    echo "    tracker.py ВСЁ ЕЩЁ ПУСТОЙ — повреждение глубже, зови на помощь"
    exit 1
fi
python3 -c "import ast,io;ast.parse(io.open('tracker.py',encoding='utf-8').read())" \
    && echo "    tracker.py цел, $(wc -c < tracker.py) байт"
echo
echo "==> Починено: $(git rev-parse --short HEAD)"
echo "Перезапустить службу (под sudo, запускай сам):"
echo "    sudo systemctl restart tracker && sleep 8 && ./status.sh"
