"""Три правила газа: пол висения, убавка на перелёте, близко и высоко.

Убавка не уходит к холостому: ниже доли висения на текущем наклоне
команда не опускается. Без измеренного висения геометрию газом не трогаем.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Висение растёт с наклоном ===")
rovno = t._visenie_na_naklone(1500, 0.0)
naklon = t._visenie_na_naklone(1500, 40.0)
assert abs(rovno - 1500) < 1.0
assert naklon > rovno
assert abs(naklon - 1500 / math.cos(math.radians(40))) < 1.0
# Дальше 60° пол не раздувается.
assert abs(t._visenie_na_naklone(1500, 80.0)
           - t._visenie_na_naklone(1500, 60.0)) < 1.0
print("    0° -> %.0f, 40° -> %.0f" % (rovno, naklon))

print("\n=== 2. Учится только на ровной высоте и живом стике ===")
t._hover_pwm = None
t._hover_logged = True
t._uchit_visenie(1400, 0)
assert abs(t._hover_pwm - 1400) < 1.0
bylo = t._hover_pwm
t._uchit_visenie(1600, 200)          # снижение, не учим
assert t._hover_pwm == bylo
t._uchit_visenie(1000, 0)            # ниже правдоподобного висения
assert t._hover_pwm == bylo
print("    висение %.0f, шум и холостой не сдвинули" % t._hover_pwm)

print("\n=== 3. Перелёт убавляет до пола, не до холостого ===")
pitched = t._visenie_na_naklone(1500, 25.0)
mode, goal = t._rezhim_gaza(pitched, 1300, 2.13, False)
floor = pitched * t.THR_DESCEND_FRACTION
assert mode == "overshoot"
assert abs(goal - floor) < 1.0, (goal, floor)
assert goal > 1200, "убавка ушла к холостому: %.0f" % goal
# Мёртвая зона и отрицательная скорость угла газ не режут.
assert t._rezhim_gaza(pitched, 1500, 0.1, False) == (None, None)
assert t._rezhim_gaza(pitched, 1500, -1.0, False) == (None, None)
print("    при 2.13 °/с цель %.0f, пол %.0f" % (goal, floor))

print("\n=== 4. Близко и высоко ===")
# 40 м и 50° — около 34 м по земле.
assert t._blizko_i_vysoko(50.0, None, 40.0)
# 40 м и 15° — далеко, газ не трогать.
assert not t._blizko_i_vysoko(15.0, None, 40.0)
# Низко и близко — обычный конец захода, не этот режим.
assert not t._blizko_i_vysoko(50.0, 20.0, 8.0)
mode, goal = t._rezhim_gaza(pitched, 1600, 0.0, True)
assert mode == "steep" and abs(goal - floor) < 1.0
# Без висения убавлять нельзя.
assert t._rezhim_gaza(None, 1300, 3.0, True) == (None, None)
print("    круто и высоко — снижение, полого — нет")

print("\n=== 5. Нос вниз ограничен ===")
assert t._ogranich_nos(1700, "steep") == 1500 + t.PITCH_STEEP_MAX_PWM
assert t._ogranich_nos(1700, "overshoot") == 1500 + t.PITCH_OVERSHOOT_MAX_PWM
assert t._ogranich_nos(1520, "steep") == 1520
assert t._ogranich_nos(1700, None) == 1700
assert t.PITCH_STEEP_MAX_PWM < t.PITCH_OVERSHOOT_MAX_PWM < t.MAX_PITCH_DEFLECT
print("    круто %d, перелёт %d" % (
    1500 + t.PITCH_STEEP_MAX_PWM, 1500 + t.PITCH_OVERSHOOT_MAX_PWM))

print("\n=== 6. Убавка не прыгает за один кадр ===")
k = 1.0
shag = t._podtyanut_gaz(1600, floor, t.THR_GEOM_SLEW_OVERSHOOT, k)
assert floor < shag < 1600
assert abs((1600 - shag) - t.THR_GEOM_SLEW_OVERSHOOT * t.NOMINAL_DT) < 0.1
print("    за кадр %.1f ШИМ, не весь провал" % (1600 - shag))

print("\nOK: газ не садится ниже доли висения и не пикирует носом")
