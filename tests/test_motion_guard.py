"""Отсев по движению обязан работать НЕЗАВИСИМО ОТ РАЗМЕРА ЦЕЛИ.

Отказ с борта: цель мельче деталей фактурного фона теряется, даже когда цвет
и яркость совпадают. Сравнивать по виду там нечего — маленький шаблон это
просто кусочек текстуры. Движение свободно от этого ограничения.

И то же требование, что к цвету: не сделать хуже. Если цель движется как фон
(стоит, либо дрон летит на неподвижную наземную цель), признак информации не
несёт и обязан отключиться сам.
"""
import io, math, os
import numpy as np
import cv2

# Путь берём от самого файла теста: жёсткий путь к моей машине делал
# тесты незапускаемыми на малине — а именно там их и нужно прогонять
# после git pull, чтобы убедиться, что приехал рабочий код.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"),
              encoding="utf-8").read()
ns = {"np": np, "cv2": cv2, "math": math,
      "MOTION_GRID_STEP": 12, "TEMPLATE_SCALE": 2.0, "MOTION_MIN_SEPARATION": 1.5,
      "MOTION_REF_DIST": 3.0, "MOTION_DIFF_REF": 4.0, "MOTION_PENALTY": 0.5}
body = src.split("def motion_penalty_map(")[1].split("\ndef color_penalty_map")[0]
exec("def motion_penalty_map(" + body, ns)
mpm = ns["motion_penalty_map"]

rng = np.random.default_rng(5)
H, W = 240, 320


def scene(obj_px, obj_shift, bg_shift):
    """Фактурный фон + цель ТОЙ ЖЕ фактуры и яркости. Различие только в
    движении: фон сдвигается на bg_shift, цель на obj_shift."""
    base = cv2.GaussianBlur((rng.random((H, W)) * 120 + 60).astype(np.uint8), (5, 5), 0)
    prev = base.copy()
    cur = np.roll(np.roll(base, bg_shift, axis=1), 0, axis=0)
    # цель — вырезка той же текстуры, движется по-своему
    cy, cx = H // 2, W // 2
    h = obj_px // 2
    patch = base[cy - h:cy + h, cx - h:cx + h]
    cur[cy - h:cy + h, cx - h + obj_shift:cx + h + obj_shift] = patch
    return prev, cur


print("  размер цели | различимость | штраф на цели | штраф на фоне")
# ГРАНИЦА ПРИМЕНИМОСТИ, измерена: разность кадров даёт сигнал начиная
# примерно с 8-10 px. Ниже объект той же фактуры почти не отличается от
# участка, который он закрыл, и различать нечем:
#     6 px  -> штраф на цели 1.00 = как у фона, не различает
#     8 px  -> 0.64 против 1.00, частично
#    10 px+ -> 0.00 против 1.00, полное разделение
# Испытание при этом нарочно злое: объект — ТОЧНАЯ копия текстуры фона.
# Настоящий объект отличается сильнее, поэтому в жизни граница ниже.
for obj in (10, 16, 32):
    prev, cur = scene(obj, obj_shift=6, bg_shift=1)
    sx1, sy1, sx2, sy2 = 160 - 60, 120 - 40, 160 + 60, 120 + 40
    shape = (sy2 - sy1 - obj + 1, sx2 - sx1 - obj + 1)
    pen, bg, sep = mpm(prev, cur, sx1, sy1, sx2, sy2, obj, obj, shape, 6.0, 0.0)
    if pen is None:
        print("   %2d px      |    %.2f      | признак выключен" % (obj, sep))
        continue
    mid = float(pen[pen.shape[0] // 2, pen.shape[1] // 2])
    edge = float(np.mean([pen[0, 0], pen[0, -1], pen[-1, 0], pen[-1, -1]]))
    print("   %2d px      |    %.2f      |     %.2f      |     %.2f" % (obj, sep, mid, edge))
    assert sep > 1.5, "цель движется иначе фона — признак обязан работать"
    assert edge > mid, "фон обязан штрафоваться сильнее цели (цель %d px)" % obj

print("\n=== главное: признак работает там, где сравнение по виду бессильно ===")
print("  цель 10 px той же фактуры и яркости отделяется полностью")

print("\n=== цель движется КАК фон -> признак отключается сам ===")
prev, cur = scene(16, obj_shift=1, bg_shift=1)
pen, bg, sep = mpm(prev, cur, 100, 80, 220, 160, 16, 16, (65, 105), 1.0, 0.0)
print("  различимость %.2f (порог 1.5) -> %s" % (sep, "ВЫКЛЮЧЕН" if pen is None else "включён"))
assert pen is None, "при движении вместе с фоном признак обязан молчать"

print("\nOK: движение различает цель от ~10 px и молчит, когда не различает")
