#!/usr/bin/env bash
# Прогнать все проверки. Работает и на Mac, и на малине — путей к конкретной
# машине внутри тестов больше нет.
#
#   ./run_tests.sh
cd "$(dirname "$0")"
fail=0
python3 tools/check_names.py tracker.py || fail=1
for t in tests/*.py; do
    if out=$(python3 "$t" 2>&1); then
        echo "  ok   $(basename "$t")"
    else
        echo "  СБОЙ $(basename "$t")"
        echo "$out" | tail -6 | sed 's/^/        /'
        fail=1
    fi
done
if [ "$fail" = 0 ]; then echo "=> всё зелёное"; else echo "=> ЕСТЬ СБОИ"; fi
exit $fail
