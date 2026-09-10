"""Версия в логе — это версия ЗАГРУЖЕННОГО кода, а не файла на диске.

Проверка версии обязана ловить ровно один случай: `git pull` сделан, служба не
перезапущена, в памяти прежний код. Раньше она его не ловила и не могла:
_code_version() читала git и md5 с диска В МОМЕНТ ЗАПИСИ, поэтому лог рапортовал
новый коммит, а status.sh сравнивал md5 диска с md5 из лога — диск сам с собой.

Замечено 9 сентября 2026: папки заходов назывались e635956 (метка берётся при
импорте и была права), а итоги внутри рапортовали f2065b3. Полтора десятка
заходов оказались сняты кодом, который считали заменённым.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

# Снимок снимается один раз, на уровне модуля.
na_urovne = [n for n in tree.body
             if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == "_VERSIYA_PRI_STARTE"
                     for t in n.targets)]
assert na_urovne, (
    "нет снимка версии при импорте: лог снова будет рапортовать то, что "
    "лежит на диске, а не то, что запущено")

# И больше _code_version() не вызывается нигде: каждый поздний вызов — это
# возврат к чтению диска задним числом.
vyzovy = [n for n in ast.walk(tree)
          if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_code_version"]
assert len(vyzovy) == 1, (
    "_code_version() вызывается %d раз. Должен ровно один — при импорте; "
    "любой поздний вызов прочитает уже подменённый диск" % len(vyzovy))

# Потребители читают снимок, а не зовут заново.
for mesto in ('self.event("ВЕРСИЯ КОДА %s" % version)',
              '"  код: %s\\n" % _VERSIYA_PRI_STARTE'):
    assert mesto in src, "потребитель версии не найден: %s" % mesto
assert "version = _VERSIYA_PRI_STARTE" in src, (
    "журнал берёт версию не из снимка")

# Метка в имени папки берётся оттуда же по смыслу — тоже один раз при импорте.
metka = [n for n in tree.body
         if isinstance(n, ast.Assign)
         and any(getattr(t, "id", None) == "KOD_METKA" for t in n.targets)]
assert metka, "метка версии для имени папки считается не при импорте"
print("версия снимается один раз при импорте; поздних вызовов нет")
