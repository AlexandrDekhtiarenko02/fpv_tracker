"""Присваивание глобалу без `global` — тихая смерть внутри камерного вызова.

Python молча делает такое имя ЛОКАЛЬНЫМ, и первое же чтение до записи даёт
UnboundLocalError. В camera_callback исключения проглатываются, поэтому
трекер просто перестаёт вести цель — без единой строки в логе. Ровно так у
нас уже пропадал оверлей и умирало управление при захвате.

py_compile этого не видит: синтаксис верный. tools/check_names.py тоже —
имя определено на верхнем уровне. Поймать можно только здесь.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tree = ast.parse(io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read())

module_names = {
    n.targets[0].id for n in tree.body
    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
}

bad = []
for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    declared = {nm for x in ast.walk(fn)
                if isinstance(x, ast.Global) for nm in x.names}
    assigned = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.AugAssign) and isinstance(x.target, ast.Name):
            assigned.add(x.target.id)
        elif isinstance(x, ast.Assign):
            for t in x.targets:
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
    for nm in sorted(assigned & module_names - declared):
        bad.append("%s(): пишет в глобал %r без объявления global" % (fn.name, nm))

print("проверено функций: %d" % len(
    [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]))
assert not bad, "\n".join(bad)
print("OK: все записи в глобалы объявлены")
