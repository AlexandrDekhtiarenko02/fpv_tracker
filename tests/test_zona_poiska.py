"""Захват берёт БЛИЖАЙШЕЕ к прицелу пятно, а на пустом фоне молчит.

Наводить перекрестье ТОЧНО на цель неудобно: аппарат трясёт, цель мелкая, и
промах на десяток пикселей означает захват фона рядом с ней. Пилот целится
«в район» — значит в этом районе и надо искать то, что выделяется.

Выбирает РАССТОЯНИЕ, а не выраженность. Выраженность отвечает на вопрос «что
тут вообще есть», а не «что пилот имел в виду»: край дороги, столб или тень
почти всегда контрастнее небольшой цели, и по яркости захват уезжал бы на них.
Поэтому выраженность работает порогом, а выбирает близость к прицелу.

Второе требование не менее важно: над однородной поверхностью зона обязана
МОЛЧАТЬ и отдавать захват ровно туда, куда навёл пилот. Иначе она из помощи
превращается в непредсказуемость.
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
             "ACQ_SNAP_PEAK_OKNO", "ACQ_SNAP_MIN_OTN", "ACQ_SNAP_MIN_ABS"):
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

print("\n=== 3. Ближнее слабое пятно побеждает дальнее яркое ===")
g = fon()
cv2.circle(g, (W // 2 + 4, H // 2 + 3), 5, 95, -1)      # рядом, поскромнее
cv2.circle(g, (W // 2 + 26, H // 2 - 8), 7, 25, -1)     # дальше, ярче
p = nayti(g)
assert p is not None, "не нашла ничего"
d_blizhe = math.hypot(p[0] - (W // 2 + 4), p[1] - (H // 2 + 3))
d_dalshe = math.hypot(p[0] - (W // 2 + 26), p[1] - (H // 2 - 8))
print("    выбрано (%d,%d): до ближнего %.1f px, до дальнего %.1f px"
      % (p[0], p[1], d_blizhe, d_dalshe))
assert d_blizhe < d_dalshe, (
    "выбрано дальнее яркое пятно: значит решает выраженность, а не близость — "
    "захват будет уезжать на край дороги вместо цели")

print("\n=== 4. Шумная крупинка ближе цели не побеждает ===")
promahi = 0
for _ in range(8):
    g = fon()
    cx, cy = W // 2 + 10, H // 2 + 6
    cv2.circle(g, (cx, cy), 6, 40, -1)
    p = nayti(g)
    if p is None or math.hypot(p[0] - cx, p[1] - cy) > 6.0:
        promahi += 1
print("    промахов мимо цели: %d из 8" % promahi)
assert promahi == 0, (
    "ближайшей оказалась не цель: порог по площади не отсекает шум")

print("\nOK: берётся ближайшее пятно, фон молчит, шум не побеждает")
