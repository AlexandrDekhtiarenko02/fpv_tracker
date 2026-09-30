"""Ambiguity/flow_gap streak даёт SOFT DISTRUST (снятие controllable),
хотя anchor arbiter блокирует PERSISTENT UNCERTAIN (найдено оператором
на реальном коде f6a10f7: комментарии и старая версия test_identity_
anchor_no_silent_switch.py утверждали, что ambiguity/flow_gap
продолжают давать soft distrust, но фактически _identity_soft_distrust
это НЕ читал — только anchor_check_streak и dual_signal_gap).

СЦЕНАРИЙ (класс, ловящий 76% реальных стендовых false-positive срывов
до этой правки): mattc почти неоднозначен (score≈second, но lead<
MATCH_LEAD_FULL), при этом immutable anchor уверенно подтверждает ту же
позицию. Хорошо, что новая архитектура НЕ переводит это в persistent
IDENTITY_UNCERTAIN (arbiter принимает anchor). Плохо, что до этой
правки controllable оставался True — два live-источника устойчиво
противоречили друг другу, но automation этого не замечало.

Требование: после IDENTITY_UNCERTAIN_CONFIRM_FRAMES подряд идущих
ambiguous кадров controllable=False, track_state=TRACKED (не persistent
UNCERTAIN), self-healing на первом чистом dual-signal кадре.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()


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

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked


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


def capture():
    with t.state_lock:
        t.aux4_state = True
    t.flow_predict = _real_flow_predict
    t.template_match_locked = _real_template_match_locked
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED


def fake_flow_still(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_ambiguous(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    # score == second → lead=0 → identity_ambiguous=1 каждый кадр
    t._match_dbg["second"] = 0.90
    return True, pred_cx, pred_cy, 0.90


def fake_match_clean(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

print("=== 1. Sustained ambiguity + real anchor confirms: до N-1 кадра "
      "controllable=True (шум не должен ронять управление сразу), на N-м "
      "кадре controllable=False (устойчивое расхождение), track_state "
      "остаётся TRACKED (arbiter блокирует persistent UNCERTAIN) ===")
capture()
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
t.flow_predict = fake_flow_still
t.template_match_locked = fake_match_ambiguous
_states = []
_controllable_seen = []
_soft_seen = []
for i in range(N + 3):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _states.append(t.track_state)
    with t.state_lock:
        _controllable_seen.append(t.target_controllable)
    _soft_seen.append(t._match_dbg.get("identity_soft_distrust"))
print("    track_state:", _states)
print("    controllable:", _controllable_seen)
print("    identity_soft_distrust:", _soft_seen)

assert all(s == t.TRACK_STATE_TRACKED for s in _states), (
    "arbiter должен был блокировать persistent UNCERTAIN, пока anchor "
    "подтверждает — получили states=%s" % _states)
assert all(_controllable_seen[:N - 1]), (
    "controllable упал раньше debounce N=%d кадров: %s"
    % (N, _controllable_seen))
assert not any(_controllable_seen[N - 1:]), (
    "controllable НЕ упал по достижении soft-порога N=%d — sustained "
    "ambiguity никак не сказалось на управлении, хотя два live-источника "
    "устойчиво противоречат друг другу: %s" % (N, _controllable_seen))
assert _soft_seen[N - 1] == 1
assert t._identity_anchor_gray is _anchor_ref
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
print("    OK: track_state=TRACKED всё время (arbiter корректно "
      "блокирует persistent UNCERTAIN), controllable=False начиная с "
      "кадра %d (soft distrust на устойчивую ambiguity)" % (N - 1))

print("\n=== 2. Self-healing: один чистый кадр возвращает controllable, "
      "без reset_tracking() ===")
t.template_match_locked = fake_match_clean
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("identity_uncertain_streak") == 0, (
    "чистый кадр не сбросил _identity_uncertain_streak")
with t.state_lock:
    _controllable_after_clean = t.target_controllable
assert _controllable_after_clean, (
    "controllable не восстановился после чистого dual-signal кадра — "
    "soft distrust не self-healing на этом пути")
assert t._match_dbg.get("identity_soft_distrust") == 0
print("    один чистый кадр -> _identity_uncertain_streak=0, "
      "controllable=True, soft_distrust=0")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: sustained ambiguity (или симметрично flow_gap — та же ветка "
      "_identity_uncertain_streak) даёт SOFT DISTRUST через IDENTITY_"
      "UNCERTAIN_CONFIRM_FRAMES кадров — снимает controllable, но не "
      "переводит в persistent UNCERTAIN, пока anchor arbiter подтверждает "
      "identity. Self-healing на первом же dual-signal кадре без явного "
      "reset_tracking().")
