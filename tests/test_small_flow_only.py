"""Мелкая рамка без пятна: положение берёт поток, матч не тянет.

Колея и яркая дорога дают высокий score. Раньше вес матча 0.22
кадр за кадром переносил рамку на этот пик. Теперь при рамке мельче
32 px и без компактного пятна центр равен предсказанию потока.
На крупной рамке вес матча остаётся.
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
t.IDENTITY_ANCHOR_CHECK_ENABLED = False
t.AUTO_TEMPLATE_REFRESH_ENABLED = False
t.SIZE_ADAPT_ENABLED = False
t.BLOB_VERIFY_ENABLED = False

CX, CY = t.LORES_W // 2, t.LORES_H // 2


def scene_with_square():
    frame = np.full((t.LORES_H, t.LORES_W), 80, np.uint8)
    frame[CY - 4:CY + 4, CX - 4:CX + 4] = 230
    return frame


_real_flow = t.flow_predict
_real_match = t.template_match_locked


def capture():
    t.flow_predict = _real_flow
    t.template_match_locked = _real_match
    scene = scene_with_square()
    with t.state_lock:
        t.aux4_state = True
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


def flat():
    return np.full((t.LORES_H, t.LORES_W), 100, np.uint8)


def run(box, flow_dx, match_dx):
    capture()
    t.lock_w = float(box)
    t.lock_h = float(box)
    t.lock_cx = float(CX)
    t.lock_cy = float(CY)
    cx0 = t.lock_cx

    def fake_flow(prev_g, cur_g, pts, cx, cy):
        return True, cx + flow_dx, cy

    def fake_match(gray, pred_cx, pred_cy, flow_motion=0.0,
                   tgt_dx=0.0, tgt_dy=0.0):
        t._match_dbg["second"] = 0.15
        t._match_dbg["psr"] = 8.0
        return True, cx0 + match_dx, float(CY), 0.92

    t.flow_predict = fake_flow
    t.template_match_locked = fake_match
    t.process_locked_tracker(flat())
    return cx0, t.lock_cx, t._match_dbg.get("w_m")


cx0, cx, w_m = run(24, 12.0, 4.0)
print("small: lock %.2f -> %.2f, w_m=%s" % (cx0, cx, w_m))
assert w_m == 0.0, "на мелкой рамке без пятна матч всё ещё двигает центр"
assert abs(cx - (cx0 + 12.0)) < 0.6, (
    "центр не совпал с потоком: %.2f, ждали %.2f" % (cx, cx0 + 12.0))
print("OK: мелкая рамка без пятна едет за потоком, не за матчем")

cx0, cx, w_m = run(48, 12.0, 4.0)
print("large: lock %.2f -> %.2f, w_m=%s" % (cx0, cx, w_m))
assert w_m is not None and w_m > 0.05, (
    "на крупной рамке вес матча обнулён: %s" % w_m)
assert abs(cx - (cx0 + 12.0)) > 0.4, (
    "крупная рамка не подтянулась к матчу: %.2f" % cx)
print("OK: крупная рамка по-прежнему смешивает поток и матч")
