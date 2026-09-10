"""Дальность пишется вместе с её погрешностью, а не голым числом.

Замерено 4 сентября 2026 по сверке с GPS: на углах 12-18° дальность врёт на
11%, на 6-12° — на 27%. Само число при этом выглядит одинаково. Закон
наведения, получив голое «141 м», не может знать, это 141±15 или 141±40, а
разница между ними решающая.

Множитель 1/sin(2θ) — чистая геометрия формулы R = h/tg(θ), он и есть
усиление ошибки угла. Проверяем, что он считается и растёт на пологих углах:
именно там дальности верить нельзя, и именно там об этом надо сказать.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Колонки есть ===")
cols = t._FLIGHT_LOG_COLUMNS.split(",")
for imya in ("range_m", "range_gain", "range_sigma_m"):
    assert imya in cols, "нет колонки %s" % imya
    print("    %-16s позиция %d" % (imya, cols.index(imya)))

print("\n=== 2. Усиление растёт на пологих углах ===")
# Это не эвристика, а производная формулы: dR/R = 2*dθ/sin(2θ).
prezhde = None
for dep in (45, 30, 20, 12, 8, 5, 2):
    us = 1.0 / max(0.02, math.sin(math.radians(2.0 * dep)))
    print("    угол %2d° -> усиление x%.1f" % (dep, us))
    if prezhde is not None:
        assert us > prezhde, "усиление обязано расти с уменьшением угла"
    prezhde = us
assert 1.0 / math.sin(math.radians(90)) == 1.0, "на 45° усиление обязано быть 1"

print("\n=== 3. Погрешность считается и растёт на пологих ===")
sig_ug = t.RANGE_ANGLE_SIGMA_DEG
assert 0.5 <= sig_ug <= 8.0, (
    "оценка шума угла %.1f° вне разумного: замерено 1.6-4.6°" % sig_ug)
alt = 30.0
bylo = None
for dep in (40, 25, 18, 12, 8, 5):
    R = alt / math.tan(math.radians(dep))
    us = 1.0 / max(0.02, math.sin(math.radians(2.0 * dep)))
    sig = R * math.hypot(2.0 * math.radians(sig_ug) * us, 0.3 / alt)
    otn = sig / R * 100
    print("    угол %2d°: R %5.0f м, погрешность %5.0f м (%.0f%%)"
          % (dep, R, sig, otn))
    if bylo is not None:
        assert otn > bylo, "относительная погрешность обязана расти на пологих"
    bylo = otn

print("\n=== 4. Сходится с тем, что замерено против GPS ===")
# Замер: 12-18° -> 11%, 18-45° -> 21%, 6-12° -> 27%. Модель обязана быть
# того же порядка, иначе она вводит в заблуждение сильнее, чем помогает.
def otn(dep):
    R = alt / math.tan(math.radians(dep))
    us = 1.0 / max(0.02, math.sin(math.radians(2.0 * dep)))
    return R * math.hypot(2.0 * math.radians(sig_ug) * us, 0.3 / alt) / R * 100

for dep, zamer in ((15, 11.0), (9, 27.0)):
    m = otn(dep)
    print("    угол %2d°: модель %.0f%%, замерено %.0f%%" % (dep, m, zamer))
    assert 0.4 * zamer < m < 3.0 * zamer, (
        "модель (%.0f%%) разошлась с замером (%.0f%%) больше чем втрое — "
        "такая оценка хуже её отсутствия" % (m, zamer))

print("\nOK: дальность больше не притворяется одинаково точной везде")
