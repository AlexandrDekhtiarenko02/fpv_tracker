"""Захват притягивается к пятну в зоне поиска, а на пустом фоне — нет.

Наводить перекрестье ТОЧНО на цель неудобно: аппарат трясёт, цель мелкая, и
промах на десяток пикселей означает захват фона рядом с ней. Пилот целится
«в район» — значит в этом районе и надо искать то, что выделяется.

Два требования, и второе не менее важно первого: над однородной поверхностью
зона обязана МОЛЧАТЬ и отдавать захват ровно туда, куда навёл пилот. Иначе
она из помощи превращается в непредсказуемость.
"""
import io
import math
import os
import re

import numpy as np
import cv2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()

ns = {"np": np, "cv2": cv2, "math": math}
for name in ("ACQ_SNAP_ENABLED", "ACQ_SNAP_SIGMA_MELKO", "ACQ_SNAP_SIGMA_KRUPNO",
             "ACQ_SNAP_EDGE_WEIGHT", "ACQ_SNAP_MIN_OTN", "ACQ_SNAP_MIN_ABS"):
    ns[name] = eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1))
W, H = 320, 240
ns["CENTER_X_LORES"], ns["CENTER_Y_LORES"] = W // 2, H // 2
ns["ACQ_SNAP_RADIUS_LORES"] = 30
exec(re.search(r"^def _nayti_pyatno.*?(?=\n\ndef )", src, re.S | re.M).group(0), ns)
nayti = ns["_nayti_pyatno"]

rng = np.random.default_rng(7)


def fon(shum=3.0):
    f = np.full((H, W), 120.0, np.float32)
    f += rng.normal(0, shum, f.shape)
    return np.clip(cv2.GaussianBlur(f, (0, 0), 1.0), 0, 255).astype(np.uint8)


print("=== 1. Цель в стороне от прицела — захват обязан к ней уехать ===")
for sdvig, storona in ((12, "вправо"), (-15, "влево")):
    g = fon()
    cx, cy = W // 2 + sdvig, H // 2 - 8
    cv2.circle(g, (cx, cy), 6, 35, -1)
    p = nayti(g)
    assert p is not None, "цель %s не найдена вовсе" % storona
    oshibka = math.hypot(p[0] - cx, p[1] - cy)
    print("    цель %s на (%d,%d): найдено (%d,%d), промах %.1f px"
          % (storona, cx, cy, p[0], p[1], oshibka))
    assert oshibka <= 5.0, (
        "промах %.1f px — захват уедет не на цель" % oshibka)

print("\n=== 2. Пустой фон — зона обязана молчать ===")
molchit = 0
for _ in range(12):
    if nayti(fon()) is None:
        molchit += 1
print("    промолчала в %d случаях из 12" % molchit)
assert molchit >= 11, (
    "на однородном фоне зона срабатывает (%d из 12 промолчала): захват станет "
    "непредсказуемым там, где пилот целится вручную" % molchit)

print("\n=== 3. Притяжение к центру: слабое пятно у края не перевешивает ===")
g = fon()
cv2.circle(g, (W // 2 + 2, H // 2), 5, 70, -1)          # у прицела, поскромнее
cv2.circle(g, (W // 2 + 28, H // 2 + 6), 5, 78, -1)     # у края, чуть ярче
p = nayti(g)
assert p is not None, "не нашла ничего"
d_centr = math.hypot(p[0] - (W // 2 + 2), p[1] - (H // 2))
d_kray = math.hypot(p[0] - (W // 2 + 28), p[1] - (H // 2 + 6))
print("    выбрано (%d,%d): до центрального %.1f px, до крайнего %.1f px"
      % (p[0], p[1], d_centr, d_kray))
assert d_centr < d_kray, (
    "выбрано пятно у края: захват будет уезжать от того, куда целился пилот")

print("\n=== 4. Явное пятно у края всё же перевешивает слабое в центре ===")
g = fon()
cv2.circle(g, (W // 2 - 1, H // 2 + 1), 4, 112, -1)     # едва заметное
cv2.circle(g, (W // 2 - 26, H // 2 - 6), 7, 25, -1)     # явное
p = nayti(g)
assert p is not None
d_kray = math.hypot(p[0] - (W // 2 - 26), p[1] - (H // 2 - 6))
print("    выбрано (%d,%d): до явного пятна %.1f px" % (p[0], p[1], d_kray))
assert d_kray <= 6.0, (
    "притяжение к центру слишком сильное: явную цель в зоне не берём")

print("\nOK: зона притягивает к цели, молчит на пустом фоне и слушает пилота")
