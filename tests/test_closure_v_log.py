"""Вся оценка сближения доезжает до лога, а не выбранные ключи.

Здесь уже обожглись: _ctl_dbg копировал из closure семь имён из четырнадцати.
Колонки tau_sigma_s, growth_raw, growth_sigma, range_gain, range_sigma_m и
pitch_bias_deg существовали в заголовке, но стояли пустыми во всех логах —
и разбор по ним показывал «величина не считается», хотя она считалась.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_estimate_closure")
klyuchi = set()
for node in ast.walk(fn):
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == "out" and isinstance(node.slice, ast.Constant)):
        klyuchi.add(node.slice.value)
    if isinstance(node, ast.Dict):
        for kk in node.keys:
            if isinstance(kk, ast.Constant) and isinstance(kk.value, str):
                klyuchi.add(kk.value)
assert len(klyuchi) >= 10, "не разобрал ключи closure: нашёл %d" % len(klyuchi)

# Словарь копируется целиком. Список имён рано или поздно отстанет от словаря.
assert "**closure," in src, (
    "closure копируется по списку имён: новый ключ снова не доедет до лога, "
    "и колонка молча останется пустой")

header = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_FLIGHT_LOG_COLUMNS" for t in node.targets):
        header = ast.literal_eval(node.value)
cols = set(header.rstrip(",").split(","))

# Каждый ключ, у которого есть одноимённая колонка, обязан в неё попадать.
# Ключи без колонки — это внутренние величины, их наличие не требуем.
poteryany = sorted(k for k in klyuchi if k in cols and 'g("%s")' % k not in src)
assert not poteryany, (
    "ключи есть в closure и в заголовке, но строка их не пишет: %s" % poteryany)

est = sorted(k for k in klyuchi if k in cols)
print("ключей в оценке сближения: %d, из них колонок в логе: %d"
      % (len(klyuchi), len(est)))
print("  " + ", ".join(est))
