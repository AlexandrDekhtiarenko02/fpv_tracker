"""Скорость по бегу земли не имеет права застревать.

В логах она стояла константой: 7.3 м/с во всех кадрах одного вылета и 9.469
во всех кадрах другого — до третьего знака, при любом наклоне от 5° до 40°,
тогда как GPS в тех же кадрах давал от 8 до 18 м/с. Это было не измерение, а
застрявшее число. А от него считается поправка тангажа, то есть и дальность.

Две причины, обе здесь и проверяются:
  * значение хранилось вечно, если измерить переставало получаться;
  * набор точек обновлялся только по счёту, поэтому в нём оседали те, что НЕ
    уехали, — то есть неудачи слежения.
"""
import ast
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

zn = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        imya = getattr(node.targets[0], "id", None)
        if imya:
            try:
                zn[imya] = ast.literal_eval(node.value)
            except Exception:
                pass

print("=== 1. Измерение протухает ===")
assert "GROUND_SPEED_STALE_S" in zn, "нет срока годности у скорости"
print("    срок годности %.1f с" % zn["GROUND_SPEED_STALE_S"])
assert 0.2 <= zn["GROUND_SPEED_STALE_S"] <= 3.0, "срок вне разумного"
assert "ground_speed_mps = None" in src, "нет сброса в None"
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and "ground" in n.name.lower())
telo = ast.get_source_segment(src, fn) or ""
assert "now_mono - _gs_speed_t > GROUND_SPEED_STALE_S" in telo, (
    "нет проверки давности: последнее удачное значение снова будет храниться "
    "вечно и выглядеть как измерение")

print("\n=== 2. Точки обновляются по времени, а не только по счёту ===")
assert "GROUND_PTS_REFRESH_S" in zn, "нет обновления набора по времени"
print("    обновление раз в %.1f с" % zn["GROUND_PTS_REFRESH_S"])
assert 0.1 <= zn["GROUND_PTS_REFRESH_S"] <= 2.0, "период вне разумного"
assert "now_mono - _gs_pts_t) >= GROUND_PTS_REFRESH_S" in telo, (
    "набор снова обновляется только по счёту: в нём осядут точки, которые не "
    "уехали, и смещение будет занижено")

print("\n=== 3. Закон не должен молча опираться на протухшее ===")
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402
t = offline.load_tracker()
# Свежего измерения нет и не было — скорость обязана быть None, а не числом.
assert t.ground_speed_mps is None, (
    "на старте скорость уже число (%s): значит где-то есть значение по "
    "умолчанию, и оно неотличимо от измеренного" % t.ground_speed_mps)
print("    на старте скорость None — по умолчанию числа нет")

print("\nOK: скорость протухает, точки обновляются, умолчания нет")
