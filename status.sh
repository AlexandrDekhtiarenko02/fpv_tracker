#!/usr/bin/env bash
# Что СЕЙЧАС летит: сверяет запущенный процесс с содержимым репозитория.
#
# Зачем: после `git pull` файл на диске меняется, а служба продолжает
# выполнять код, загруженный в память при старте. Снаружи разницы не видно —
# и легко улететь со старой версией, будучи уверенным в новой. Здесь это
# проверяется по факту, а не на веру.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Репозиторий ==="
HEAD_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
echo "  коммит на диске : $HEAD_SHA  $(git log -1 --pretty=%s 2>/dev/null)"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "  ВНИМАНИЕ: есть несохранённые правки (git status)"
fi
FILE_MD5=$(md5sum tracker.py 2>/dev/null | cut -c1-12)
echo "  md5 tracker.py  : $FILE_MD5"

echo
echo "=== Служба ==="
ACTIVE=$(systemctl is-active tracker 2>/dev/null || echo "нет")
MAINPID=$(systemctl show tracker -p MainPID --value 2>/dev/null || echo 0)
echo "  состояние       : $ACTIVE"
echo "  PID             : $MAINPID"
echo "  работает с      : $(systemctl show tracker -p ActiveEnterTimestamp --value 2>/dev/null)"
echo "  перезапусков    : $(systemctl show tracker -p NRestarts --value 2>/dev/null)"

echo
echo "=== Что реально запущено ==="
# Берём САМЫЙ СВЕЖИЙ журнал событий и убеждаемся, что он принадлежит текущему
# процессу: иначе можно случайно прочитать версию с прошлого запуска.
EVT=$(ls -t flight_logs/*.events.log 2>/dev/null | head -1)
if [ -z "${EVT:-}" ]; then
    echo "  Логов нет — трекер ещё ни разу не стартовал после установки."
    exit 0
fi
LOG_PID=$(grep -m1 "session start pid=" "$EVT" | sed 's/.*pid=//')
RUN_LINE=$(grep -m1 "ВЕРСИЯ КОДА" "$EVT" | sed 's/.*ВЕРСИЯ КОДА //')

if [ "$LOG_PID" != "$MAINPID" ]; then
    echo "  Свежий лог принадлежит процессу $LOG_PID, а служба крутит $MAINPID."
    echo "  Значит служба перезапускалась и лог ещё не создан, либо трекер"
    echo "  запущен не службой. Подожди пару секунд и повтори."
    exit 1
fi
if [ -z "$RUN_LINE" ]; then
    # Запущенная версия старше самого штампа версии — она про него не знает.
    echo "  Процесс $MAINPID не сообщил версию: он запущен кодом, в котором"
    echo "  штампа версии ещё не было."
    echo
    echo "=== Вывод ==="
    echo "  ЗАПУЩЕН УСТАРЕВШИЙ КОД (старее, чем коммит $HEAD_SHA)."
    echo "      sudo systemctl restart tracker"
    exit 1
fi
echo "  $RUN_LINE"

RUN_MD5=$(echo "$RUN_LINE" | sed -n 's/.*md5=\([0-9a-f]*\).*/\1/p')
echo
echo "=== Вывод ==="
if echo "$RUN_LINE" | grep -q "ИЗМЕНЁН-ПОВЕРХ-КОММИТА"; then
    echo "  Летит файл, ИЗМЕНЁННЫЙ поверх коммита $HEAD_SHA."
    echo "  Правки есть только здесь — в репозитории их нет. Сохрани их"
    echo "  (git add/commit/push), иначе они потеряются."
elif [ "$RUN_MD5" = "$FILE_MD5" ]; then
    echo "  СОВПАДАЕТ: запущен ровно тот код, что лежит в репозитории ($HEAD_SHA)."
else
    echo "  РАСХОЖДЕНИЕ: на диске md5=$FILE_MD5, а запущен md5=$RUN_MD5."
    echo "  Так бывает после git pull без перезапуска службы — в памяти"
    echo "  осталась прежняя версия. Лечится одной командой:"
    echo "      sudo systemctl restart tracker"
fi
