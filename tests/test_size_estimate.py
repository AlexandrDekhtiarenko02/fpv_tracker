"""Оценщик размера обязан измерять КРУПНУЮ цель, а не подменять её минимумом.

Отказ с борта: на крупном фактурном объекте фон перебивает. Причина — коробка
стояла на значении по умолчанию (8 px) в 76% кадров. Оценщик смотрел область
25x25 px и отвергал компоненту шире 19.2 px, возвращая при этом РАЗМЕР ПО
УМОЛЧАНИЮ, то есть самый маленький. Адаптация его применяла и каждые 10
кадров ужимала коробку обратно к минимуму — ровно на тех целях, где она
должна была расти.
"""
import io
import os
import numpy as np
import cv2

# Путь берём от самого файла теста: жёсткий путь к моей машине делал
# тесты незапускаемыми на малине — а именно там их и нужно прогонять
# после git pull, чтобы убедиться, что приехал рабочий код.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"),
              encoding="utf-8").read()


def load(radius_max, adaptive):
    ns = {"np": np, "cv2": cv2, "_size_fail": {"why": 0, "R": 0, "w": 0},
          "ACQ_SIZE_SEARCH_RADIUS": 12, "SIZE_SEARCH_RADIUS_MAX": radius_max,
          "ACQ_DEFAULT_LOCK_W": 8, "ACQ_DEFAULT_LOCK_H": 8,
          "LOCK_PAD": 2.05, "LOCK_MIN_W": 5, "LOCK_MIN_H": 5,
          "LOCK_MAX_W": 38, "LOCK_MAX_H": 38}
    exec("def clamp(v, lo, hi):\n    return lo if v < lo else (hi if v > hi else v)", ns)
    body = src.split("def estimate_size_at_position(")[1].split(
        "\ndef estimate_size_at_crosshair")[0]
    exec("def estimate_size_at_position(" + body, ns)
    return ns["estimate_size_at_position"]


def scene(obj_px):
    """Светлый объект заданного размера на тёмном фоне, в центре кадра."""
    g = np.full((240, 320), 40, np.uint8)
    h = obj_px // 2
    g[120 - h:120 + h, 160 - h:160 + h] = 200
    return g


est = load(40, True)
print("  размер цели | без подсказки | с текущим размером коробки")
for obj in (10, 16, 24, 32, 44):
    g = scene(obj)
    w_blind, _ = est(g, 160, 120)                     # как звалось раньше
    w_hint, _ = est(g, 160, 120, cur_w=obj)           # область под цель
    f = lambda v: ("не знаю" if v is None else "%.0f px" % v)
    print("   %2d px      |    %-8s   |   %s" % (obj, f(w_blind), f(w_hint)))

print("\n=== главное: крупная цель больше НЕ подменяется минимумом ===")
for obj in (24, 32, 44):
    w, h = est(scene(obj), 160, 120)
    assert w is None, "без подсказки крупная цель должна давать «не знаю», а не 8"
    w2, h2 = est(scene(obj), 160, 120, cur_w=obj)
    assert w2 is not None and w2 > 8, "с подсказкой обязана измеряться: %s" % w2
    print("  цель %2d px -> измерена как %.0f px (было бы 8)" % (obj, w2))

print("\n=== мелкая цель по-прежнему измеряется как раньше ===")
for obj in (10, 16):
    w, _ = est(scene(obj), 160, 120)
    assert w is not None and w > 8
    print("  цель %2d px -> %.0f px" % (obj, w))

print("\nOK: 'не смог измерить' больше не выдаётся за 'цель минимального размера'")
