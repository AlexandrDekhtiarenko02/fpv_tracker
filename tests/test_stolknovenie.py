"""Курс столкновения: скорость угла даёт ШИМ, рамка только остаётся в кадре."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import offline

t = offline.load_tracker()
now = 1000.0

assert t.COLLISION_ENABLED

pwm = t._stolknovenie_pwm(
    2.13, now, now, t.LOS_RATE_DEADBAND_DPS,
    t.COLLISION_PITCH_PWM_DPS, t.COLLISION_PITCH_MAX, t.PITCH_SIGN)
zhdu = t.PITCH_SIGN * t.COLLISION_PITCH_PWM_DPS * (2.13 - t.LOS_RATE_DEADBAND_DPS)
assert abs(pwm - zhdu) < 1e-6, (pwm, zhdu)
assert pwm > 0, "растущий угол должен опускать нос"

assert t._stolknovenie_pwm(2.13, now - 10, now, 0.3, 20, 80, 1) == 0
assert t._stolknovenie_pwm(0.1, now, now, 0.3, 20, 80, 1) == 0
assert t._stolknovenie_pwm(20.0, now, now, 0.3, 20, 80, 1) == 80
assert t._stolknovenie_pwm(-3.0, now, now, 0.5, 12, 70, t.ROLL_SIGN) < 0

kadr = t._kadr_oshibka(40.0, 1.0) * t.COLLISION_PITCH_PIXEL
assert 0 < kadr < 20, kadr
assert t.COLLISION_ROLL_PIXEL < t.COLLISION_PITCH_PIXEL

src = open(os.path.join(os.path.dirname(__file__), "..", "tracker.py"),
           encoding="utf-8").read()
assert "rate_damp_pwm + col_p" in src
assert "roll_offset += col_r" not in src
assert "yaw_offset += col_r" not in src
assert "float(adx_y)" in src
print("ok  pitch %.0f PWM на 2.13 °/с, рамка даёт %.0f px из 40" % (pwm, kadr))
