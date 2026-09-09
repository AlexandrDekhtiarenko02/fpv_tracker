"""Перекрестье прячется на время захвата.

Наведение идёт по постоянному пеленгу: цель обязана СТОЯТЬ в кадре, а не
сидеть в центре. Перекрестье в это время показывает величину, к которой контур
не стремится, и читается как промах там, где всё правильно.
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(ROOT, "tracker.py"), encoding="utf-8").read()
tree = ast.parse(src)

fn = [n for n in ast.walk(tree)
      if isinstance(n, ast.FunctionDef) and n.name == "draw_overlay_on_frame"]
assert fn, "не нашёл draw_overlay_on_frame"
telo = ast.get_source_segment(src, fn[0]) or ""

assert "draw_crosshair(frame)" in telo, "перекрестье не рисуется вовсе"
assert "if not (vis and box is not None):" in telo, (
    "перекрестье рисуется безусловно — на захвате оно будет сбивать")

# Рисование обязано стоять ПОСЛЕ чтения состояния, иначе условие не на чем
# строить.
i_sost = telo.index("vis = target_visible")
i_ris = telo.index("draw_crosshair(frame)")
assert i_ris > i_sost, "перекрестье рисуется до того, как известно про захват"

# И возвращаться должно само, без отдельного сброса: условие — отсутствие
# захвата, а не событие.
assert telo.count("draw_crosshair(frame)") == 1, (
    "перекрестье рисуется в двух местах — одно из них рано или поздно "
    "разойдётся с условием")
print("перекрестье прячется на захвате и возвращается без лока")
