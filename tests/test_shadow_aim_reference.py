"""AimReference (ТЗ §4): единая явная структура для того, что уже
считается в dx_aim/dy_aim, без нового расчёта.

Инвариант, который и доказывает, что breakdown ничего не поменял:
box_cx - shadow_ref_x == dx_aim
box_cy - shadow_ref_y == dy_aim

Если это верно всегда (в разных условиях: с lead, с pitch_comp, с
LOS-поправкой, без них) — значит shadow_ref_x/y это ТА ЖЕ математика,
только явно поименованная по вкладам (ref_static + ref_att + ref_lead +
ref_other), а не альтернативный расчёт, который мог бы разойтись с
live dx_aim/dy_aim.
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


def kadr(dx, dy, fc_pitch=15.0, score=0.85):
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
        t.app_state["fc_pitch_deg"] = fc_pitch
        t.app_state["fc_pitch_ts"] = t.time.monotonic()
        t.app_state["gyro"] = (3, 3, 3)
        t.app_state["imu_ts"] = t.time.monotonic()
    _clk.tick(FRAME_DT)
    t.update_control_from_target()
    return t._ctl_dbg, t._shadow_ctl_dbg


print("=== 1. box_cx - shadow_ref_x == dx_aim на нескольких разных "
      "сценариях (lead/pitch_comp/без них) ===")
force_reset()
_clk.t = 1000.0
_sluchai = [
    (0, 0, 15.0), (40, 30, 15.0), (40, 30, 25.0),  # с pitch_comp
    (-60, -40, 15.0), (90, 60, 10.0), (10, 5, 20.0),
]
max_rasxod_x = 0.0
max_rasxod_y = 0.0
for dx, dy, fc_pitch in _sluchai:
    c, sc = kadr(dx, dy, fc_pitch=fc_pitch)
    assert sc.get("active"), "shadow неактивен на управляемом кадре"
    box_cx, box_cy = c["box_cx"], c["box_cy"]
    _err_x = box_cx - sc["ref_x"]
    _err_y = box_cy - sc["ref_y"]
    rx = abs(_err_x - c["dx_aim"])
    ry = abs(_err_y - c["dy_aim"])
    max_rasxod_x = max(max_rasxod_x, rx)
    max_rasxod_y = max(max_rasxod_y, ry)
    assert rx < 1e-9, (
        "box_cx - shadow_ref_x (%.6f) != dx_aim (%.6f) при dx=%d dy=%d "
        "fc_pitch=%.1f — breakdown разошёлся с live-математикой"
        % (_err_x, c["dx_aim"], dx, dy, fc_pitch))
    assert ry < 1e-9, (
        "box_cy - shadow_ref_y (%.6f) != dy_aim (%.6f) при dx=%d dy=%d "
        "fc_pitch=%.1f" % (_err_y, c["dy_aim"], dx, dy, fc_pitch))
print("    %d сценариев, макс. расхождение X=%.2e Y=%.2e (float-погрешность)"
      % (len(_sluchai), max_rasxod_x, max_rasxod_y))

print("\n=== 2. shadow_err_x/y тоже равны dx_aim/dy_aim (то же тождество, "
      "записанное явно) ===")
c, sc = kadr(50, 35, fc_pitch=18.0)
assert abs(sc["err_x"] - c["dx_aim"]) < 1e-9
assert abs(sc["err_y"] - c["dy_aim"]) < 1e-9
print("    shadow_err_x=%.3f == dx_aim=%.3f, shadow_err_y=%.3f == "
      "dy_aim=%.3f" % (sc["err_x"], c["dx_aim"], sc["err_y"], c["dy_aim"]))

print("\n=== 3. Разложение по X суммируется в ref_x, по Y — в ref_y ===")
_summa_x = (t.CENTER_X - sc["ref_static_x"] - sc["ref_att_x"]
           - sc["ref_lead_x"] - sc["ref_other_x"])
_summa_y = (t.CENTER_Y - sc["ref_static_y"] - sc["ref_att_y"]
           - sc["ref_lead_y"] - sc["ref_other_y"])
assert abs(_summa_x - sc["ref_x"]) < 1e-9
assert abs(_summa_y - sc["ref_y"]) < 1e-9
print("    CENTER - static - att - lead - other == ref по обеим осям")

print("\n=== 4. ref_att_x всегда 0 (компенсация тангажа не влияет на X "
      "в этом законе) ===")
assert sc["ref_att_x"] == 0.0, (
    "ref_att_x != 0 — компенсация тангажа неожиданно появилась на "
    "горизонтальной оси; либо это новый вклад (тогда breakdown полный), "
    "либо ошибка в разложении")
print("    ref_att_x=0.0, как и в текущем законе (pitch_comp только по Y)")

print("\nOK: AimReference — та же математика dx_aim/dy_aim, явно "
      "разложенная по вкладам, тождество box_cx - ref_x == dx_aim "
      "держится всегда")
