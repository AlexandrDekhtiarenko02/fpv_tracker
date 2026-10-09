"""Мелкая контрастная цель: центр рамки — пятно, а не шаг потока.

Поток и матч в этом кадре согласны уйти на 15 px. Раньше пятно
возвращало центр не больше чем на 3 px, и рамка оставалась на траве.
Теперь найденное компактное пятно забирает центр целиком.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import offline  # noqa: E402

t = offline.load_tracker()
t.MOTION_GUARD_ENABLED = False
t.IDENTITY_UNCERTAIN_ENABLED = False
t.AUTO_TEMPLATE_REFRESH_ENABLED = False
t.SIZE_ADAPT_ENABLED = False

CX, CY = t.LORES_W // 2, t.LORES_H // 2


def scene_with_square():
    frame = np.full((t.LORES_H, t.LORES_W), 80, np.uint8)
    frame[CY - 4:CY + 4, CX - 4:CX + 4] = 230
    return frame


scene = scene_with_square()
with t.state_lock:
    t.aux4_state = True
t.acq_wait_left = 0
t.prev_aux_on = True
t.track_state = t.TRACK_STATE_ACQ
t.process_locked_tracker(scene)
assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"
t.lock_w = 24.0
t.lock_h = 24.0
t.lock_cx = float(CX)
t.lock_cy = float(CY)
cx0, cy0 = t.lock_cx, t.lock_cy


def fake_flow(prev_g, cur_g, pts, cx, cy):
    return True, cx + 15.0, cy


def fake_match(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.2
    t._match_dbg["psr"] = 8.0
    return True, pred_cx, pred_cy, 0.95


t.flow_predict = fake_flow
t.template_match_locked = fake_match
t.process_locked_tracker(scene)
print("lock %.2f,%.2f -> %.2f,%.2f" % (cx0, cy0, t.lock_cx, t.lock_cy))
assert abs(t.lock_cx - CX) < 3.0 and abs(t.lock_cy - CY) < 3.0, (
    "пятно не забрало центр: рамка ушла на %.1f,%.1f" % (t.lock_cx, t.lock_cy))
print("OK: согласованный шаг потока на 15 px не снял рамку с белого пятна")

rng = np.random.default_rng(0)
grass = (rng.random((48, 48)) * 30 + 90).astype(np.uint8)
assert t._small_blob_center(grass, 24, 24, 24, 24) is None, (
    "ровная текстура дала пятно — на траве центр начнёт прыгать")
print("OK: на ровной текстуре пятна нет, шаг потока не подменяется")
