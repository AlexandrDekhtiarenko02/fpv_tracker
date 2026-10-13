"""Тест висения не требует захвата и пишет число после ровной высоты."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import offline

t = offline.load_tracker()
put = os.path.join(tempfile.mkdtemp(), "local_settings.py")
t._nastroiki_put = put
t._hover_pwm = None
t.HOVER_PWM = None

with t.state_lock:
    t.app_state["armed"] = False
assert t._menu_hover_test() == "arm and hold"
assert t._hover_test

t._hover_test_shag(1400, 0, False, 0.5)
assert t._hover_test_t == 0.0

t._hover_test_shag(1000, 0, True, 0.5)
assert t._hover_test_status == "thr low"
assert not t._hover_test_gotovo

t._hover_test_shag(1400, 180, True, 0.5)
assert t._hover_test_status.startswith("not level")
assert t._hover_test_t == 0.0

for _ in range(5):
    t._hover_test_shag(1420, 10, True, 0.5)
assert t._hover_test_t == 2.5
assert not t._hover_test_gotovo

t._hover_test_shag(1460, -10, True, 0.5)
assert t._hover_test_gotovo
assert t._hover_pwm == 1427, t._hover_pwm
assert "HOVER_PWM = 1427" in open(put, encoding="utf-8").read()

t._hover_test_shag(1500, 0, False, 0.1)
assert t._hover_test_status.startswith("saved")
print("ok", t._hover_test_status)
