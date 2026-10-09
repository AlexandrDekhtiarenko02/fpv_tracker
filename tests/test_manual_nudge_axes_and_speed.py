"""Направления осей и длина броска manual nudge.

Подтверждает явно, по отдельности:
  A. Roll вправо (AUX2>1500)  -> рамка едет ВПРАВО  (dx > 0)
  B. Roll влево  (AUX2<1500)  -> рамка едет ВЛЕВО   (dx < 0)
  C. Pitch «вверх» (AUX3>1500) -> рамка едет ВВЕРХ по кадру (dy < 0 — в
     координатах изображения y растёт ВНИЗ, значит «вверх» это МЕНЬШЕ y).
  D. Pitch «вниз» (AUX3<1500)  -> рамка едет ВНИЗ по кадру (dy > 0)
  E. Смещение — доля хода стика до упора, не скорость. Полный упор
     даёт MANUAL_NUDGE_THROW_PX за один кадр.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

AUX2_IDX = t.MANUAL_NUDGE_ROLL_AUX_IDX
AUX3_IDX = t.MANUAL_NUDGE_PITCH_AUX_IDX


class _Chasy:
    """Управляемые часы. Без них _nudge_prev_t считает интервал по РЕАЛЬНОМУ
    времени выполнения теста — доли миллисекунды между двумя вызовами в
    одном процессе Python дают почти нулевой шаг, а не искомый шаг за
    номинальный кадр."""

    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def tick(self, dt):
        self.t += dt


_clk = _Chasy()
t.time.monotonic = _clk.monotonic
FRAME_DT = 1.0 / t.CAM_FPS


def make_scene(seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((t.LORES_H, t.LORES_W), 120, np.uint8)
    cx, cy = t.LORES_W // 2, t.LORES_H // 2
    s = 24
    obj = (rng.random((s, s)) * 100 + 100).astype(np.uint8)
    cv2.circle(obj, (s // 3, s // 3), s // 5, 40, -1)
    frame[cy - s // 2:cy + s // 2, cx - s // 2:cx + s // 2] = obj
    return frame


scene = make_scene()


def set_stick(roll_us=0.0, pitch_us=0.0):
    with t.state_lock:
        ch = [1500] * 8
        ch[AUX2_IDX] = 1500 + roll_us
        ch[AUX3_IDX] = 1500 + pitch_us
        t.app_state["rc_channels"] = ch
        t.app_state["rc_link_ts"] = t.time.monotonic()


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    set_stick()
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def one_nudge_step(roll_us=0.0, pitch_us=0.0):
    """Один кадр коррекции из НЕЙТРАЛЬНОГО состояния. _nudge_prev_t
    обнуляется явно ПЕРЕД коррекцией — иначе он мог бы унаследоваться от
    предыдущего вызова через ACQ-кадр capture() (тот не проходит гейт
    track_state==TRACKED и потому не сбрасывает часы сам), и шаг считался
    бы по чужому интервалу, а не по номинальному dt=1/CAM_FPS."""
    capture()
    t._nudge_prev_t = None
    set_stick(roll_us, pitch_us)
    cx0, cy0 = t.lock_cx, t.lock_cy
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    assert t._match_dbg.get("manual_nudge") == 1, "nudge не активировался"
    return t.lock_cx - cx0, t.lock_cy - cy0


STICK = 450.0  # за дедбендом 300: избыток 150 из 200 до упора

print("=== A/B. Roll: право/лево соответствуют рамке ===")
dx_right, _ = one_nudge_step(roll_us=+STICK)
dx_left, _ = one_nudge_step(roll_us=-STICK)
print("    roll=+%.0f -> dx=%+.3f px" % (STICK, dx_right))
print("    roll=-%.0f -> dx=%+.3f px" % (STICK, dx_left))
assert dx_right > 0, "стик вправо не двигает рамку вправо (dx=%.3f)" % dx_right
assert dx_left < 0, "стик влево не двигает рамку влево (dx=%.3f)" % dx_left
assert abs(dx_right - abs(dx_left)) < 1e-6, (
    "право/лево не симметричны по величине: %.4f vs %.4f" % (dx_right, abs(dx_left)))

print("\n=== C/D. Pitch: вверх/вниз соответствуют рамке (было инвертировано) ===")
_, dy_up = one_nudge_step(pitch_us=+STICK)
_, dy_down = one_nudge_step(pitch_us=-STICK)
print("    AUX3=+%.0f (\"вверх\") -> dy=%+.3f px" % (STICK, dy_up))
print("    AUX3=-%.0f (\"вниз\")  -> dy=%+.3f px" % (STICK, dy_down))
# В координатах изображения y растёт ВНИЗ: «рамка едет вверх по кадру»
# значит dy ОТРИЦАТЕЛЬНЫЙ. Раньше (MANUAL_NUDGE_PITCH_SIGN=+1) знак был
# обратный — сюда идёт прямая проверка того, что жалоба со стенда закрыта.
assert dy_up < 0, (
    "AUX3 в сторону \"вверх\" не двигает рамку вверх по кадру (dy=%.3f) — "
    "ось тангажа снова инвертирована" % dy_up)
assert dy_down > 0, (
    "AUX3 в сторону \"вниз\" не двигает рамку вниз по кадру (dy=%.3f)"
    % dy_down)
assert t.MANUAL_NUDGE_PITCH_SIGN == -1, (
    "MANUAL_NUDGE_PITCH_SIGN=%r — ожидался -1 после инверсии по стендовой "
    "обратной связи" % t.MANUAL_NUDGE_PITCH_SIGN)

print("\n=== E. Бросок, не скорость: доля хода до упора, упор = THROW_PX ===")
travel = t.MANUAL_NUDGE_STICK_HALF_US - t.MANUAL_NUDGE_DEADBAND_US
frac = (STICK - t.MANUAL_NUDGE_DEADBAND_US) / travel
expected = frac * t.MANUAL_NUDGE_THROW_PX
print("    THROW_PX = %.1f, стик %.0f us -> %.2f px" % (
    t.MANUAL_NUDGE_THROW_PX, STICK, expected))
assert abs(dx_right - expected) < 1e-6, (
    "вправо %.4f, формула броска %.4f" % (dx_right, expected))
assert abs(t.MANUAL_NUDGE_THROW_PX - 24.0) < 1e-6

capture()
t._nudge_prev_t = None
set_stick(t.MANUAL_NUDGE_STICK_HALF_US, 0.0)
cx0 = t.lock_cx
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
full = t.lock_cx - cx0
print("    упор -> %+.3f px" % full)
assert abs(full - t.MANUAL_NUDGE_THROW_PX) < 1e-6, (
    "полный ход стика дал %.3f, ожидалось THROW_PX" % full)

print("\nOK: roll вправо/влево и pitch вверх/вниз соответствуют движению "
      "рамки, длина броска — доля хода стика до упора")
