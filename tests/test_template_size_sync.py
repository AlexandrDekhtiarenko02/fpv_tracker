"""Размер эталона в расчёте центра обязан совпадать с самим эталоном.

ЧТО БЫЛО. build_template ВСЕГДА записывает новый размер в глобалы tmpl_w и
tmpl_h — даже когда вызывающая сторона его результат ВЫБРАСЫВАЕТ. А
выбрасывает она его каждый раз, когда размер коробки изменился: обновление
эталона идёт только при совпадении форм, а ветка пересчёта выключена флагом
TEMPLATE_RESCALE_ON_SIZE_CHANGE = False.

Центр совпадения считается как sx1 + mx + tmpl_w / 2. Если tmpl_w говорит 80,
а эталон на самом деле 40 — центр смещается на (80 - 40) / 2 = 20 пикселей.
Систематически, в одну сторону, каждый кадр. Это и есть 'рамка уезжает на
край'.

Проявилось в полный рост, когда примерка масштабов стала менять размер
коробки постоянно: 96 раз за заход 165310.
"""
import ast
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "template_match_locked":
        fn = node
assert fn is not None, "не нашёл template_match_locked"

# Внутри функции размер обязан быть взят у самого эталона до любого его
# использования. Иначе расхождение вернётся при следующей правке.
lines = src.splitlines()
body_src = "\n".join(lines[fn.lineno - 1:fn.end_lineno])
sync = "tmpl_h, tmpl_w = template_gray.shape[:2]"
assert sync in body_src, (
    "template_match_locked не берёт размер у самого эталона — "
    "глобалы tmpl_w/tmpl_h могут разойтись с template_gray")

i_sync = body_src.index(sync)
for use in ("tmpl_w / 2.0", "sw = int(tmpl_w", "matchTemplate"):
    i_use = body_src.find(use)
    if i_use >= 0:
        assert i_use > i_sync, (
            "%r используется РАНЬШЕ, чем размер взят у эталона" % use)

print("размер берётся у самого эталона до первого использования")

# И обратная сторона: build_template по-прежнему пишет в глобалы, значит
# полагаться на них где-то ещё нельзя без такой же синхронизации.
bt = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "build_template":
        bt = node
assert bt is not None
bt_src = "\n".join(lines[bt.lineno - 1:bt.end_lineno])
assert "tmpl_w = tmpl.shape[1]" in bt_src, "build_template изменился, проверь тест"
print("build_template по-прежнему пишет глобалы — синхронизация обязательна")

print("\nOK: расхождение размера эталона с расчётом центра невозможно")
