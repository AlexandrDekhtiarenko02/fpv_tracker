"""Roll/Yaw contention (ТЗ §7): диагностика, как часто оба контура
одновременно правят один горизонтальный dx.

Не переписывает закон Roll/Yaw (само ТЗ явно требует это НЕ делать) —
только считает и логирует: знак и величину вклада каждой оси от dx, и
флаг roll_yaw_both_active, когда обе оси ЗАМЕТНО (не на уровне шума)
тянут в ОДНУ сторону.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
CX, CY = t.CENTER_X, t.CENTER_Y


class _Chasy:
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS


def force_reset():
    with t.state_lock:
        t.target_controllable = False
        t.target_box_main = None
    t.update_control_from_target()


def kadr(dx, dy=0, score=0.85):
    with t.state_lock:
        t.target_box_main = (CX - 20 + dx, CY - 20 + dy,
                             CX + 20 + dx, CY + 20 + dy)
        t.target_controllable = True
        t.target_visible = True
    t.last_match_score = score
    t._match_dbg = {"psr": 6.0}
    t.lock_w0 = t.lock_h0 = 30.0
    with t.state_lock:
        t.app_state["rc_throttle"] = 1450
        t.app_state["rc_throttle_ts"] = t.time.monotonic()
        t.app_state["fc_pitch_deg"] = 15.0
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (0, 0, 0)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.update_control_from_target()
    return t._shadow_ctl_dbg


print("=== 1. Крупный устойчивый dx вправо: roll и yaw оба заметно "
      "активны и с одним знаком -> both_active=True ===")
force_reset()
sc = None
for _ in range(10):
    sc = kadr(dx=200)   # заметное, устойчивое смещение вправо
print("    sign_roll=%d sign_yaw=%d mag_roll=%.1f mag_yaw=%.1f "
      "both_active=%s" % (sc["sign_roll"], sc["sign_yaw"], sc["mag_roll"],
                          sc["mag_yaw"], sc["roll_yaw_both_active"]))
assert sc["sign_roll"] > 0, "цель справа не даёт roll_off > 0"
assert sc["sign_yaw"] > 0, "цель справа не даёт yaw_pd > 0"
assert sc["roll_yaw_both_active"], (
    "устойчивое крупное смещение вправо — оба контура тянут в одну "
    "сторону, both_active должен быть True")

print("\n=== 2. Цель почти в центре: обе оси близки к нулю -> "
      "both_active=False ===")
force_reset()
sc = None
for _ in range(5):
    sc = kadr(dx=1)
print("    mag_roll=%.2f mag_yaw=%.2f both_active=%s"
      % (sc["mag_roll"], sc["mag_yaw"], sc["roll_yaw_both_active"]))
assert not sc["roll_yaw_both_active"], (
    "цель у центра — ни одна ось не должна считаться 'заметно активной'")

print("\n=== 3. sign_roll/sign_yaw соответствуют направлению — влево "
      "даёт противоположный знак ===")
force_reset()
sc_right = None
for _ in range(10):
    sc_right = kadr(dx=200)
force_reset()
sc_left = None
for _ in range(10):
    sc_left = kadr(dx=-200)
print("    вправо: sign_roll=%d sign_yaw=%d | влево: sign_roll=%d sign_yaw=%d"
      % (sc_right["sign_roll"], sc_right["sign_yaw"],
         sc_left["sign_roll"], sc_left["sign_yaw"]))
assert sc_right["sign_roll"] == -sc_left["sign_roll"]
assert sc_right["sign_yaw"] == -sc_left["sign_yaw"]
assert sc_left["roll_yaw_both_active"], (
    "устойчивое крупное смещение влево тоже должно давать both_active")

print("\n=== 4. yaw_weight гасит yaw при большой |dx| (уже в live-законе) "
      "— both_active должен это уважать, не спорить с ним ===")
# YAW_ROLL_CROSSOVER/YAW_ROLL_BLEND_RANGE — существующий закон ослабления
# yaw на больших dx. Проверяем не конкретное число, а то, что диагностика
# не противоречит: если yaw_weight увёл yaw_pd к нулю, both_active не
# может быть True просто по инерции прошлого кадра.
force_reset()
sc = None
for _ in range(15):
    # Очень большое dx — за YAW_ROLL_CROSSOVER+BLEND_RANGE, если они малы
    # относительно 300, yaw должен быть сильно ослаблен.
    sc = kadr(dx=300)
print("    dx=300: mag_yaw=%.2f (yaw_weight ослабляет yaw на большом dx)"
      % sc["mag_yaw"])
# Не жёсткий assert на конкретное значение (зависит от настроек весов),
# просто убеждаемся, что диагностика не падает и остаётся согласованной
# (both_active не может быть True, если mag_yaw ниже порога).
if sc["mag_yaw"] < t.SHADOW_CONTENTION_FRAC * t.MAX_YAW_DEFLECT:
    assert not sc["roll_yaw_both_active"], (
        "mag_yaw ниже порога активности, а both_active всё равно True — "
        "несогласованность в самой диагностике")
print("    диагностика согласована с уже существующим yaw_weight")

print("\nOK: диагностика конкуренции roll/yaw корректно отражает "
      "направление, величину и порог значимости обеих осей")
