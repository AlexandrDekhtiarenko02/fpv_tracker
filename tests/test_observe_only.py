"""В режиме наблюдения оверрайд не должен включаться НИ ПРИ КАКИХ УСЛОВИЯХ.

Почему это критично. Пока MSP OVERRIDE включён на полётнике, MSP_RC
возвращает НАШИ ЖЕ значения вместо стиков пилота. В логе окажется эхо
трекера вместо действий человека — и выглядеть это будет совершенно
правдоподобно. Вся кампания вылетов уйдёт впустую, а понять это будет не по
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

print("\n=== 3. В режиме пилота на моторы уходят только стики, не команды ===")
# Молчание здесь вредно: на Betaflight 4.5 при включённом MSP OVERRIDE
# последняя команда держится бесконечно. Поэтому поток есть, но оси
# трекера в него не подставляются.
lines = src.splitlines()
send_lines = [i for i, line in enumerate(lines)
              if "send_msp_set_raw_rc(channels)" in line
              and not line.strip().startswith("def ")]
assert send_lines, "в коде не найден вызов send_msp_set_raw_rc"
observe_passthrough = []
for i, line in enumerate(lines):
    if "build_output_channels(" not in line:
        continue
    window = "\n".join(lines[i:i + 3])
    prev = "\n".join(lines[max(0, i - 20):i])
    if "False)" in window and "OBSERVE_ONLY" in prev:
        observe_passthrough.append(i)
assert observe_passthrough, (
    "в режиме пилота нет пасстру стиков (build_output_channels с apply_ov=False)")
# Команды трекера собираются только в обычной ветке, не под OBSERVE_ONLY.
for i, line in enumerate(lines):
    if line.strip().startswith("def "):
        continue
    if "build_output_channels(" not in line:
        continue
    block = "\n".join(lines[i:i + 2])
    if "r_cmd" not in block:
        continue
    window = "\n".join(lines[max(0, i - 20):i + 1])
    assert "elif have_fresh_rc:" in window, (
        "команды трекера собираются вне обычной ветки, их может унести "
        "режим пилота")
print("    команды трекера в режим пилота не входят")

print("\n=== 4. Флаг существует и по умолчанию ВЫКЛЮЧЕН ===")
m = re.search(r"^OBSERVE_ONLY = (.+?)(?:\s+#.*)?$", src, re.M)
assert m, "нет флага OBSERVE_ONLY"
val = eval(m.group(1))
print("    OBSERVE_ONLY =", val)
assert val is False, (
    "режим наблюдения включён по умолчанию — тогда серийной заход пройдёт БЕЗ "
    "управления, и это обнаружится только в воздухе")

print("\n=== 5. Программа разбирается и флаг читается однозначно ===")
tree = ast.parse(src)
assigns = [n for n in tree.body
           if isinstance(n, ast.Assign)
           and any(getattr(t, "id", None) == "OBSERVE_ONLY" for t in n.targets)]
print("    присваиваний OBSERVE_ONLY на верхнем уровне:", len(assigns))
assert len(assigns) == 1, "флаг задан больше одного раза — легко перепутать"

print("\nOK: в режиме пилота на моторы уходят стики, не команды трекера")
