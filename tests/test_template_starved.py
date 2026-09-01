"""Эталон не имеет права остаться внутри однородного предмета.

СЛУЧАЙ ОПЕРАТОРА: чёрная точка на белом фоне, при приближении рамка уезжает с
цели. Промоделировано пошагово:

    точка   коробка  эталон   разброс яркости в эталоне
     18       18     36x36        85.2
     40       18     36x36        64.4
     50       18     36x36         6.0
     62       18     36x36         0.0   <- эталон ЦЕЛИКОМ ВНУТРИ точки
    120       18     36x36         0.0

Когда предмет вырастает больше эталона, эталон оказывается внутри него и
становится ровным квадратом без единого признака. Совпадать с ним одинаково
хорошо будет любое ровное место кадра — рамка и уезжает куда угодно.

Замкнутый круг: примерка масштабов на безликом эталоне тоже даёт ровно 1.0,
коробка не растёт, эталон навсегда остаётся внутри предмета.

Однородный предмет — ХУДШИЙ случай, а не редкий: внутренних признаков у него
нет вовсе, держаться можно только за КРАЙ. Значит край обязан попадать в
эталон, а для этого эталон должен быть шире предмета.
"""
import io, os, re
import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()


def const(name):
    return eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1),
                {"TRACK_SCALE": 1})


TS = const("TEMPLATE_SCALE")
TMIN, TMAX = const("TEMPLATE_MIN"), const("TEMPLATE_MAX")
STARVED = const("TEMPLATE_STARVED_STD")
GROW = const("TEMPLATE_STARVED_GROW")
LOCK_MIN, LOCK_MAX = const("LOCK_MIN_W"), const("LOCK_MAX_W")


def dot(d):
    g = np.full((240, 320), 235, np.uint8)
    cv2.circle(g, (160, 120), d // 2, 20, -1)
    return g


def template_std_for(box, d):
    t = int(max(TMIN, min(TMAX, box * TS)))
    r = t // 2
    return float(np.std(dot(d)[120 - r:120 + r, 160 - r:160 + r])), t


print("=== 1. Без правки эталон навсегда остаётся внутри точки ===")
box = 18.0
for d in (18, 40, 62, 96, 120):
    std, t = template_std_for(box, d)
    print("    точка %3d px, коробка %3.0f, эталон %2dx%2d -> разброс %.1f"
          % (d, box, t, t, std))
std, _ = template_std_for(18.0, 120)
assert std < STARVED, "модель не воспроизводит случай оператора"

print("\n=== 2. С правкой коробка растёт, пока край не вернётся в эталон ===")
box = 18.0
for d in (18, 40, 62, 96, 120):
    for _ in range(20):
        std, t = template_std_for(box, d)
        if std >= STARVED:
            break
        box = min(LOCK_MAX, box * GROW)
    std, t = template_std_for(box, d)
    print("    точка %3d px -> коробка %5.1f, эталон %2dx%2d, разброс %.1f"
          % (d, box, t, t, std))
    assert std >= STARVED, (
        "на точке %d px эталон остался безликим (разброс %.1f)" % (d, std))

print("\n=== 3. Рост ограничен и не уходит в бесконечность ===")
assert box <= LOCK_MAX, "коробка перевалила предел: %.0f > %d" % (box, LOCK_MAX)
print("    коробка %.0f при пределе %d" % (box, LOCK_MAX))

print("\n=== 4. На фактурном предмете правка молчит ===")
rng = np.random.default_rng(4)
g = np.clip(120 + cv2.GaussianBlur(rng.normal(0, 25, (240, 320)), (0, 0), 1.5) * 3,
            0, 255).astype(np.uint8)
t = int(max(TMIN, min(TMAX, 18.0 * TS)))
r = t // 2
std = float(np.std(g[120 - r:120 + r, 160 - r:160 + r]))
print("    фактурный предмет -> разброс %.1f (порог %.1f)" % (std, STARVED))
assert std >= STARVED, "правка сработает там, где не нужно"

print("\nOK: безликий эталон распознаётся и расширяется до края предмета")
