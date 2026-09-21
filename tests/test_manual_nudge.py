"""Ручная коррекция рамки правым стиком (ТЗ next-commit spec §11/§12).

Во время TRACKED+override каналы roll/pitch и так подменяются PID-выходом
трекера — полётник живой стик пилота на этих осях не видит вовсе. Этот
стик, ничего не делающий для полётника в данный момент, становится входом
для ручной коррекции рамки: отклонение стика задаёт СКОРОСТЬ сдвига (не
абсолютную координату), а на отпускании происходит мягкая перепривязка
внутри TRACKED — без LOST/ACQ, без потери эталона.

Проверяется: во время коррекции рамка движется ровно по формуле стика (а
не куда-то ещё от flow/match, которые в это время не должны работать),
после отпускания рвётся geometry_epoch и на один кадр замораживается
адаптация шаблона (позиция ещё не подтверждена свежим измерением).
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()


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

with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"
cx0 = t.lock_cx
epoch0 = t.geometry_epoch
print("захват: lock_cx=%.2f geometry_epoch=%d" % (cx0, epoch0))


def set_stick(roll_us):
    with t.state_lock:
        t.app_state["receiver_channels"] = [
            1500 + roll_us, 1500, 1500, 1500, 1500, 1500, 1500, 1500]


print("\n=== A. Стик в мёртвой зоне — nudge неактивен ===")
set_stick(0)
before = t.lock_cx
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0, (
    "nudge посчитан активным при стике в центре")
print("    manual_nudge=%s, lock_cx %.2f -> %.2f"
      % (t._match_dbg.get("manual_nudge"), before, t.lock_cx))

print("\n=== B. Стик отклонён вправо — рамка едет по формуле, а не от "
      "flow/match ===")
ROLL_US = 300.0  # заметно за пределами MANUAL_NUDGE_DEADBAND_US=60
set_stick(ROLL_US)
half = 500.0 - t.MANUAL_NUDGE_DEADBAND_US
norm = (ROLL_US - t.MANUAL_NUDGE_DEADBAND_US) / half
expected_step = t.MANUAL_NUDGE_ROLL_SIGN * norm * t.MANUAL_NUDGE_MAX_PX_S / t.CAM_FPS
print("    ожидаемый шаг за кадр: %.4f px" % expected_step)

positions = [t.lock_cx]
N = 5
for _ in range(N):
    t.process_locked_tracker(scene)
    assert t._match_dbg.get("manual_nudge") == 1, "nudge не распознан активным"
    assert t.overlay_text == "NUDGE", (
        "оверлей не показывает NUDGE во время коррекции: %r" % t.overlay_text)
    positions.append(t.lock_cx)

steps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
print("    шаги lock_cx:", ["%.4f" % s for s in steps])
for s in steps:
    assert abs(s - expected_step) < 1e-6, (
        "шаг рамки (%.4f) не совпадает с формулой стика (%.4f) — либо "
        "nudge считает неверно, либо flow/match всё-таки вмешались"
        % (s, expected_step))
assert t.geometry_epoch == epoch0, (
    "geometry_epoch изменился ВО ВРЕМЯ коррекции — re-anchor должен "
    "случиться только на ОТПУСКАНИИ стика")

print("\n=== C. Отпускание стика — re-anchor: новая эпоха, заморозка "
      "адаптации, сброс истории ===")
cx_before_release = t.lock_cx
set_stick(0)
t.process_locked_tracker(scene)
assert t._match_dbg.get("manual_nudge") == 0, "nudge не снялся при отпускании"
print("    geometry_epoch: %d -> %d" % (epoch0, t.geometry_epoch))
assert t.geometry_epoch == epoch0 + 1, (
    "отпускание стика не подняло geometry_epoch — re-anchor не сработал")
assert t._adapt_frozen_posle_reanchor, (
    "адаптация шаблона не заморожена сразу после re-anchor — позиция ещё "
    "не подтверждена свежим измерением")
assert abs(t.lock_cx - cx_before_release) < 1.0, (
    "re-anchor сам по себе не должен телепортировать рамку — она уже там, "
    "где её оставила ручная коррекция")

print("\n=== D. Один кадр со свежим потоком — заморозка снимается ===")
t.process_locked_tracker(scene)
assert not t._adapt_frozen_posle_reanchor, (
    "заморозка адаптации не снялась после кадра со свежим потоком — "
    "шаблон никогда не сможет доучиться после re-anchor")

print("\nOK: ручная коррекция двигает рамку по формуле стика, flow/match "
      "не вмешиваются во время неё, отпускание вызывает re-anchor с новой "
      "эпохой и временной заморозкой адаптации")
