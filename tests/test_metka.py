"""Метка захвата: квадрат, оборот и поджатие за отведённое время.

Движение здесь не украшение. Захват — единственное событие, которое пилот
задаёт сам, и подтверждение ему нужно мгновенное: в очках, на трясущейся
картинке, статичная метка от статичной рамки почти неотличима.
"""
import math
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Лупа уменьшена ===")
print("    MAG_SIZE = %d" % t.MAG_SIZE)
assert t.MAG_SIZE <= 140, (
    "лупа %d px — она закрывает обзор там, где он нужен для наведения"
    % t.MAG_SIZE)

print("\n=== 2. Метка тонкая и в конце мельче прежнего ромба ===")
print("    толщина %d, радиус %g -> %g за %g с"
      % (t.METKA_TOLSHCHINA, t.METKA_R_START, t.METKA_R_END, t.METKA_ANIM_S))
assert t.METKA_TOLSHCHINA == 1, "метка толще линии: она указывает, а не заменяет цель"
assert t.METKA_R_END < t.METKA_R_START, "метка не поджимается"
assert t.METKA_R_START == 14.0, (
    "начальный радиус разошёлся с прежним ромбом: метка перестанет приходить "
    "той величины, к которой глаз привык")
assert 0.2 <= t.METKA_ANIM_S <= 1.0, "длительность вне разумного"


def ugly_i_radius(kadr):
    """Углы квадрата, вытащенные из нарисованного кадра."""
    ys, xs = np.nonzero(kadr[:, :, 0] > 200)
    assert len(xs) > 0, "метка не нарисована"
    cx, cy = 160.0, 120.0
    r = np.hypot(xs - cx, ys - cy)
    return float(r.max())


BOX = (140, 100, 180, 140)   # центр 160,120


def snyat(dolya):
    """Кадр в заданной доле анимации."""
    t._metka_t0 = t.time.monotonic() - dolya * t.METKA_ANIM_S
    f = np.zeros((240, 320, 3), np.uint8)
    t.draw_corners(f, BOX, t.COLOR_WHITE, 2)
    return f


print("\n=== 3. Радиус убывает по ходу анимации ===")
radiusy = []
for d in (0.0, 0.25, 0.5, 0.75, 1.0):
    r = ugly_i_radius(snyat(d))
    radiusy.append(r)
    print("    доля %.2f -> радиус %.1f px" % (d, r))
assert radiusy[0] > radiusy[-1], "метка не уменьшилась"
for i in range(1, len(radiusy)):
    assert radiusy[i] <= radiusy[i - 1] + 1.5, (
        "радиус вырос на доле %.2f: движение обязано быть в одну сторону"
        % (i * 0.25))
assert abs(radiusy[0] - t.METKA_R_START) <= 2.0, (
    "в начале радиус %.1f вместо %g" % (radiusy[0], t.METKA_R_START))
assert abs(radiusy[-1] - t.METKA_R_END) <= 2.0, (
    "в конце радиус %.1f вместо %g" % (radiusy[-1], t.METKA_R_END))

print("\n=== 4. Оборот ровно один: в конце квадрат стоит ровно ===")
# В покое углы на 45°, 135°, 225°, 315° — то есть стороны горизонтальны.
f = snyat(1.0)
ys, xs = np.nonzero(f[:, :, 0] > 200)
shirina = xs.max() - xs.min()
vysota = ys.max() - ys.min()
print("    в конце: ширина %d, высота %d" % (shirina, vysota))
assert abs(shirina - vysota) <= 2, (
    "квадрат в конце стоит на ребре: оборот не целый")
# А в середине — повёрнут.
f = snyat(0.45)
ys, xs = np.nonzero(f[:, :, 0] > 200)
print("    в середине: ширина %d, высота %d" % (xs.max()-xs.min(), ys.max()-ys.min()))

print("\n=== 5. После анимации метка неподвижна ===")
r1 = ugly_i_radius(snyat(1.5))
r2 = ugly_i_radius(snyat(4.0))
print("    через 1.5 и 4 длительности: %.1f и %.1f px" % (r1, r2))
assert abs(r1 - r2) < 0.6, "метка продолжает двигаться после анимации"

print("\n=== 6. Стороны втягиваются: остаются уголки, середина открыта ===")


def seredina_storony_pusta(kadr, r):
    """Есть ли разрыв посередине сторон.

    В покое квадрат стоит ровно, значит середины сторон — это точки строго
    справа, слева, сверху и снизу от центра на расстоянии r/sqrt(2).
    """
    cx, cy = 160, 120
    pol = r / math.sqrt(2.0)
    pusto = 0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        x = int(round(cx + dx * pol))
        y = int(round(cy + dy * pol))
        okno = kadr[max(0, y - 1):y + 2, max(0, x - 1):x + 2, 0]
        if okno.size and okno.max() < 200:
            pusto += 1
    return pusto


# Сразу после оборота стороны ещё сплошные.
f = snyat(1.0)
r_kon = t.METKA_R_END
tselyh = seredina_storony_pusta(f, r_kon)
print("    сразу после оборота: пустых середин %d из 4" % tselyh)
assert tselyh == 0, "стороны разорвались раньше времени — два движения слились"

# А после втягивания — пусты.
t._metka_t0 = t.time.monotonic() - (t.METKA_ANIM_S + t.METKA_UGLY_S + 0.05)
f = np.zeros((240, 320, 3), np.uint8)
t.draw_corners(f, BOX, t.COLOR_WHITE, 2)
pusto = seredina_storony_pusta(f, r_kon)
print("    после втягивания:    пустых середин %d из 4" % pusto)
assert pusto == 4, (
    "середины сторон закрашены: квадрат остался сплошным и закрывает цель")

# Но углы на месте — иначе метка просто исчезла.
ys, xs = np.nonzero(f[:, :, 0] > 200)
assert len(xs) > 0, "метка исчезла целиком"
r_ugly = float(np.hypot(xs - 160.0, ys - 120.0).max())
print("    углы на радиусе %.1f px (метка %g)" % (r_ugly, r_kon))
assert abs(r_ugly - r_kon) <= 2.0, "углы уехали с радиуса метки"

assert 0.0 < t.METKA_UGOL_DOLYA < 0.5, (
    "доля стороны %g: при 0.5 квадрат сплошной, при 0 углов не останется"
    % t.METKA_UGOL_DOLYA)

print("\nOK: лупа меньше, метка делает оборот, поджимается и раскрывается в уголки")
