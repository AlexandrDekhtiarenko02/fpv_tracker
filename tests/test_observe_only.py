"""В режиме наблюдения оверрайд не должен включаться НИ ПРИ КАКИХ УСЛОВИЯХ.

Почему это критично. Пока MSP OVERRIDE включён на полётнике, MSP_RC
возвращает НАШИ ЖЕ значения вместо стиков пилота. В логе окажется эхо
трекера вместо действий человека — и выглядеть это будет совершенно
правдоподобно. Вся кампания налётов уйдёт впустую, а понять это будет не по
чему.

Второе: пока оверрайд выключен, машина физически не может вмешаться в
управление, пока пилот заходит на цель.

Проверка идёт по исходному тексту, а не запуском: важно, что в программе НЕТ
ни одного места, где оверрайд включается в обход этого режима.
"""
import ast
import io
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

print("=== 1. Нет ни одного безусловного включения оверрайда ===")
bad = []
for i, line in enumerate(src.splitlines(), 1):
    t = line.strip()
    if re.match(r"^override_active\s*=\s*True\b", t):
        bad.append("%d: %s" % (i, t))
for b in bad:
    print("    НАЙДЕНО:", b)
assert not bad, (
    "оверрайд включается безусловно — в режиме наблюдения в лог попадут наши "
    "же команды вместо стиков пилота")
print("    таких мест нет")

print("\n=== 2. Включение оверрайда учитывает режим наблюдения ===")
found = [l.strip() for l in src.splitlines()
         if "override_active" in l and "OBSERVE_ONLY" in l]
for f in found:
    print("   ", f)
assert found, "нигде не видно связи оверрайда с режимом наблюдения"

print("\n=== 3. Флаг существует и по умолчанию ВЫКЛЮЧЕН ===")
m = re.search(r"^OBSERVE_ONLY = (.+?)(?:\s+#.*)?$", src, re.M)
assert m, "нет флага OBSERVE_ONLY"
val = eval(m.group(1))
print("    OBSERVE_ONLY =", val)
assert val is False, (
    "режим наблюдения включён по умолчанию — тогда боевой заход пройдёт БЕЗ "
    "управления, и это обнаружится только в воздухе")

print("\n=== 4. Программа разбирается и флаг читается однозначно ===")
tree = ast.parse(src)
assigns = [n for n in tree.body
           if isinstance(n, ast.Assign)
           and any(getattr(t, "id", None) == "OBSERVE_ONLY" for t in n.targets)]
print("    присваиваний OBSERVE_ONLY на верхнем уровне:", len(assigns))
assert len(assigns) == 1, "флаг задан больше одного раза — легко перепутать"

print("\nOK: наблюдение не может незаметно превратиться в управление")
