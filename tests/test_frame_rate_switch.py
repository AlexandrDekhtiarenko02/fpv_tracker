"""Вне захвата кадр короткий, в захвате ровно 20 к/с. Оба края равны."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()

print("=== 1. Захват — 50 мс ===")
for sost in (t.TRACK_STATE_TRACKED, t.TRACK_STATE_HOLD,
             t.TRACK_STATE_IDENTITY_UNCERTAIN, t.TRACK_STATE_VISUAL_UNSTABLE):
    us = t._dlitelnost_kadra(sost)
    assert us == t.LOCK_FRAME_US == 50000, (sost, us)
print("    TRACKED/HOLD/UNCERTAIN/UNSTABLE = 50000")

print("\n=== 2. Вне захвата кадр не короче затвора и не короче пола ===")
t._cam_idle_us = None
t._cam_exp_us = 29990
assert t._dlitelnost_kadra(t.TRACK_STATE_IDLE) == 29990
t._cam_exp_us = 8000
assert t._dlitelnost_kadra(t.TRACK_STATE_IDLE) == t.IDLE_FRAME_FLOOR_US
t._cam_exp_us = None
assert t._dlitelnost_kadra(t.TRACK_STATE_LOST) == 33000
assert t._dlitelnost_kadra(t.TRACK_STATE_ACQ) == 33000
print("    выдержка 30 мс -> 30 мс, выдержка 8 мс -> пол 16 мс")

print("\n=== 3. Зафиксированный простой кадр не едет за живым замером ===")
t._cam_idle_us = 18000
t._cam_exp_us = 4000
assert t._dlitelnost_kadra(t.TRACK_STATE_IDLE) == 18000
assert t._dlitelnost_kadra(t.TRACK_STATE_TRACKED) == 50000
print("    простой 18000, захват всё равно 50000")

print("\nOK: частота захвата 20 к/с, вне захвата по затвору")
