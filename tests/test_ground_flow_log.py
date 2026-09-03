"""Сырой бег земли и свежесть GPS должны доходить до полётного CSV."""
import os
import sys
import time

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import offline  # noqa: E402

t = offline.load_tracker()
rng = np.random.RandomState(17)
first = (rng.rand(240, 320) * 255).astype(np.uint8)
second = cv2.warpAffine(first, np.float32([[1, 0, 2], [0, 1, 3]]),
                        (320, 240), flags=cv2.INTER_LINEAR)

t._gs_prev_gray = None
t._gs_prev_pts = None
t._gs_prev_t = 0.0
t.ground_speed_mps = None
with t.state_lock:
    t.app_state.update(armed=True, alt_cm=1000, fc_pitch_deg=5.0)

print("=== 1. Сохраняется измеренное движение пикселей ===")
t.estimate_ground_speed(first, 10.00)
t.estimate_ground_speed(second, 10.04)
d = t._ground_flow_dbg
print("    dx=%.2f dy=%.2f px/s=%.1f points=%d dt=%.1f ms"
      % (d["dx_px"], d["dy_px"], d["px_s"], d["points"], d["dt_ms"]))
assert abs(d["dx_px"] - 2.0) < 0.5
assert abs(d["dy_px"] - 3.0) < 0.5
assert 75.0 < d["px_s"] < 105.0
assert d["points"] >= t.GROUND_MIN_POINTS
assert abs(d["dt_ms"] - 40.0) < 0.1

print("\n=== 2. Эти величины и возраст GPS попадают в строку CSV ===")
captured = []
t.flight_log.enabled = True
t.flight_log._t0 = time.monotonic()
t.flight_log.row = captured.append
t.flight_log.event = lambda *_: None
t.lock_log.active = False
with t.state_lock:
    t.track_state = t.TRACK_STATE_IDLE
    t.app_state["gps_ts"] = time.monotonic() - 0.12
t._capture_flight_row(time.monotonic())
t.flight_log.enabled = False
assert captured, "строка лога не собралась"
row = dict(zip(t._COLS, captured[-1]))
for key, value in (
        ("ground_flow_dx_px", d["dx_px"]),
        ("ground_flow_dy_px", d["dy_px"]),
        ("ground_flow_px_s", d["px_s"]),
        ("ground_flow_points", d["points"]),
        ("ground_flow_dt_ms", d["dt_ms"])):
    assert row[key] == value, "%s не дошёл до CSV" % key
print("    gps_age_ms=%.1f" % row["gps_age_ms"])
assert 100.0 <= row["gps_age_ms"] <= 300.0, "возраст GPS записан неверно"

print("\nOK: поток можно пересчитать после полёта без разбора видео")
