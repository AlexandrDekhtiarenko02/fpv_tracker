"""Путевая скорость по бегу земли — то, что заменяет GPS.

Закон точки прицеливания зависит от скорости. GPS даёт её напрямую, но GPS
может не быть, а высота и камера есть всегда.

Точка на земле уходит назад тем быстрее, чем быстрее летим и чем ниже. Зная
высоту и угол, под которым точка видна, получаем наклонную дальность до неё, а
из неё и скорость:

    v = (угловая скорость точки) * (наклонная дальность)

Здесь проверяется сама арифметика этого пересчёта: если земля бежит с такой-то
скоростью в пикселях, скорость обязана получиться правильной.
"""
import io, os, re, math

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()


def const(name, ns=None):
    return eval(re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M).group(1),
                dict(ns or {}))


VFOV = const("CAMERA_VFOV_DEG")
TILT = const("CAMERA_TILT_DEG")
MAIN_H = const("MAIN_W, MAIN_H")[1] if False else eval(
    re.search(r"^MAIN_W, MAIN_H = (.+?)(?:\s+#.*)?$", src, re.M).group(1))[1]
BAND_TOP = const("GROUND_BAND_TOP")
MIN_DEP = const("GROUND_MIN_DEPRESSION_DEG")

LORES_H = 240
CENTER_Y = MAIN_H / 2.0
DEG_PER_PX = VFOV / float(MAIN_H)


def speed_from_shift(shift_px, dt, alt_m, fc_pitch):
    """Тот же пересчёт, что в трекере."""
    y0 = int(LORES_H * BAND_TOP)
    band_mid = (y0 + LORES_H) / 2.0
    off_deg = (band_mid * 2.0 - CENTER_Y) * DEG_PER_PX
    dep = fc_pitch - TILT + off_deg
    if dep < MIN_DEP or alt_m <= 0.5:
        return None, dep
    slant = alt_m / math.sin(math.radians(dep))
    ang_rate = math.radians(shift_px * 2.0 * DEG_PER_PX) / dt
    return ang_rate * slant, dep


print("=== 1. Пересчёт даёт верную скорость ===")
print("    %-10s %-10s %-12s %s" % ("высота", "тангаж", "бег земли", "скорость"))
for alt, pitch, shift in ((50, 5.0, 3.0), (50, 5.0, 6.0), (100, 5.0, 3.0),
                          (25, 5.0, 3.0)):
    v, dep = speed_from_shift(shift, 1 / 25.0, alt, pitch)
    print("    %-10s %-10s %-12s %s"
          % ("%d м" % alt, "%.0f°" % pitch, "%.0f px/кадр" % shift,
             "%.1f м/с" % v if v else "не считается"))

print("\n=== 2. Вдвое быстрее бежит — вдвое больше скорость ===")
v1, _ = speed_from_shift(3.0, 1 / 25.0, 50, 5.0)
v2, _ = speed_from_shift(6.0, 1 / 25.0, 50, 5.0)
print("    3 px/кадр -> %.1f м/с, 6 px/кадр -> %.1f м/с" % (v1, v2))
assert abs(v2 / v1 - 2.0) < 0.01, "нелинейно по бегу земли"

print("\n=== 3. Вдвое выше — вдвое больше скорость при том же беге ===")
v3, _ = speed_from_shift(3.0, 1 / 25.0, 100, 5.0)
print("    50 м -> %.1f м/с, 100 м -> %.1f м/с" % (v1, v3))
assert abs(v3 / v1 - 2.0) < 0.01, "нелинейно по высоте"

print("\n=== 4. У самого горизонта не считаем — дальность там врёт ===")
v4, dep = speed_from_shift(3.0, 1 / 25.0, 50, -20.0)
print("    тангаж -20° -> угол на землю %.1f° (нужно >= %.0f°) -> %s"
      % (dep, MIN_DEP, "не считается" if v4 is None else "%.1f м/с" % v4))
assert v4 is None, "считаем скорость там, где дальность до земли недостоверна"

print("\n=== 5. Без высоты не считаем вовсе ===")
v5, _ = speed_from_shift(3.0, 1 / 25.0, 0.2, 5.0)
assert v5 is None, "посчитали скорость без пригодной высоты"
print("    высота 0.2 м -> не считается")

print("\nOK: скорость выводится из бега земли, высоты и угла — без GPS")
