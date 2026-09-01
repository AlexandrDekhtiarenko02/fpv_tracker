"""В репозитории не должно быть путей к конкретной машине.

Такой путь не ломает ничего на Mac и потому проходит незамеченным, а на
малине превращает проверку в FileNotFoundError — ровно там, где она нужнее
всего: сразу после git pull, до перезапуска службы.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = re.compile(r"/(Users|home)/[A-Za-z0-9_.-]+/")

found = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames
                   if d not in (".git", "flight_logs", "__pycache__")]
    for name in filenames:
        if not name.endswith((".py", ".sh")):
            continue
        path = os.path.join(dirpath, name)
        for i, line in enumerate(io.open(path, encoding="utf-8", errors="replace"), 1):
            if line.lstrip().startswith("#"):
                continue          # в пояснениях путь как пример допустим
            m = BAD.search(line)
            if m:
                found.append("%s:%d: %s" % (
                    os.path.relpath(path, ROOT), i, line.strip()[:90]))

assert not found, "пути к конкретной машине:\n" + "\n".join(found)
print("OK: путей к конкретной машине нет")
