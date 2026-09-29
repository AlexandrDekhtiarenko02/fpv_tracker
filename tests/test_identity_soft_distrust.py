"""SOFT DISTRUST (найдено оператором на реальном коде bf7957f, не
отчёте): debounce (IDENTITY_ANCHOR_CHECK_CONFIRM_N=3 x 0.5с ≈ 1.5с,
IDENTITY_DUAL_SIGNAL_GAP_MAX_FRAMES=60 ≈ 2.5с) — правильный инструмент
для решения "когда становиться PERSISTENT IDENTITY_UNCERTAIN" (одиночный
CV-шум не должен ронять лок), но НЕ оправдание держать controllable=True
всё это время: K-4 честно показывает, что lock уже мог доехать до
непроверенной позиции ДО того, как debounce вообще набрался.

Разделено на два разных таймера: PERSISTENT track_state=IDENTITY_UNCERTAIN
(debounce, как и было) и НЕМЕДЛЕННОЕ снятие controllable (на первом же
реально измеренном anchor mismatch / на начале долгого single-signal
разрыва) — тем же централизованным механизмом в _update_control_from_
target_impl(), что уже применён к _identity_uncertain_pending, но БЕЗ
sticky-персистентности: self-healing на следующем же успешном замере/
dual-signal кадре, без reset_tracking().
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
import numpy as np
import cv2
import offline  # noqa: E402

t = offline.load_tracker()

_real_flow_predict = t.flow_predict
_real_template_match_locked = t.template_match_locked
_real_shadow_match = t._shadow_match_against_template


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

assert t.IDENTITY_SOFT_DISTRUST_ENABLED, (
    "тест сам по себе негоден без IDENTITY_SOFT_DISTRUST_ENABLED")


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
    t._shadow_match_against_template = _real_shadow_match
    t.acq_wait_left = 0
    t.prev_aux_on = True
    t.track_state = t.TRACK_STATE_ACQ
    _clk.tick(0.001)
    t.process_locked_tracker(scene)
    assert t.track_state == t.TRACK_STATE_TRACKED, "захват не состоялся"


print("=== A. Anchor-check: ОДИН неудачный замер снимает controllable "
      "НЕМЕДЛЕННО (streak=1 << CONFIRM_N=%d), track_state остаётся "
      "TRACKED — persistent-состояние debounce ещё не набрал ==="
      % t.IDENTITY_ANCHOR_CHECK_CONFIRM_N)
capture()
_anchor_ref = t._identity_anchor_gray


def fake_shadow_bad(gray, tmpl, tmpl_w, tmpl_h, tmpl_std, pred_cx, pred_cy, flow_motion):
    # Слабый score РОВНО в запрошенной позиции — не позиционная слепота
    # (это отдельный, уже закрытый вопрос, test_identity_anchor_position_
    # blindness.py), а честный "anchor замер сейчас не подтвердил".
    return True, 0.10, 1.0, 0.05, pred_cx, pred_cy


t._shadow_match_against_template = fake_shadow_bad
_clk.tick(t.IDENTITY_ANCHOR_CHECK_PERIOD_S)
t.process_locked_tracker(scene)
assert t._match_dbg.get("identity_anchor_check_streak") == 1, (
    "тест сам по себе негоден: первый замер не дал streak=1")
assert t.track_state == t.TRACK_STATE_TRACKED, (
    "track_state=%r после ОДНОГО неудачного замера — persistent-debounce "
    "не должен был сработать так рано" % t.track_state)
with t.state_lock:
    _controllable_a1 = t.target_controllable
assert not _controllable_a1, (
    "controllable остался True после первого же измеренного anchor "
    "mismatch — soft distrust не сработал немедленно")
assert t._match_dbg.get("identity_soft_distrust") == 1
print("    1 неудачный замер (streak=1) -> track_state=TRACKED (persistent "
      "debounce=%d ещё не набран), controllable=False (soft distrust "
      "сработал немедленно)" % t.IDENTITY_ANCHOR_CHECK_CONFIRM_N)

print("\n=== B. Anchor-check: SELF-HEALING — следующий же успешный замер "
      "восстанавливает controllable БЕЗ reset_tracking() ===")
t._shadow_match_against_template = _real_shadow_match
_clk.tick(t.IDENTITY_ANCHOR_CHECK_PERIOD_S)
t.process_locked_tracker(scene)
assert t._match_dbg.get("identity_anchor_check_streak") == 0, (
    "тест сам по себе негоден: успешный замер не сбросил streak")
assert t.track_state == t.TRACK_STATE_TRACKED
with t.state_lock:
    _controllable_a2 = t.target_controllable
assert _controllable_a2, (
    "controllable НЕ восстановился после успешного anchor-замера — "
    "soft distrust не self-healing")
assert t._match_dbg.get("identity_soft_distrust") == 0
print("    следующий успешный замер (streak=0) -> controllable=True "
      "восстановлен сам, никакого reset_tracking() не понадобилось")

print("\n=== C. Anchor-check: N=%d неудачных замеров подряд — ПЕРСИСТЕНТНЫЙ "
      "IDENTITY_UNCERTAIN, controllable уже был False ЗАДОЛГО до этого "
      "===" % t.IDENTITY_ANCHOR_CHECK_CONFIRM_N)
t._shadow_match_against_template = fake_shadow_bad
_controllable_seen_c = []
for i in range(t.IDENTITY_ANCHOR_CHECK_CONFIRM_N):
    _clk.tick(t.IDENTITY_ANCHOR_CHECK_PERIOD_S)
    t.process_locked_tracker(scene)
    with t.state_lock:
        _controllable_seen_c.append(t.target_controllable)
assert not any(_controllable_seen_c), (
    "controllable стал True хотя бы раз за серию неудачных замеров: %s"
    % _controllable_seen_c)
assert t.track_state == t.TRACK_STATE_IDENTITY_UNCERTAIN, (
    "track_state=%r после N=%d неудачных замеров подряд, ожидали "
    "persistent IDENTITY_UNCERTAIN" % (t.track_state, t.IDENTITY_ANCHOR_CHECK_CONFIRM_N)
)
assert t._identity_anchor_gray is _anchor_ref
print("    controllable=False уже с ПЕРВОГО замера серии — persistent "
      "IDENTITY_UNCERTAIN наступил только на N-м, но не добавил новой "
      "потери управления, она уже была")

t._shadow_match_against_template = _real_shadow_match
t.reset_tracking(to_acq=False)

print("\n=== D. Dual-signal gap: только-flow дольше "
      "IDENTITY_UNCERTAIN_CONFIRM_FRAMES=%d (СИЛЬНО МЕНЬШЕ persistent-"
      "порога %d) снимает controllable НЕМЕДЛЕННО, track_state остаётся "
      "TRACKED ===" % (t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES,
                       t.IDENTITY_DUAL_SIGNAL_GAP_MAX_FRAMES))
assert t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES < t.IDENTITY_DUAL_SIGNAL_GAP_MAX_FRAMES, (
    "тест предполагает, что soft-порог для gap заметно меньше "
    "persistent-порога")
t._orig_anchor_check_d = t.IDENTITY_ANCHOR_CHECK_ENABLED
t.IDENTITY_ANCHOR_CHECK_ENABLED = False   # изолируем — эта секция про gap


def fake_flow_only(prev_g, cur_g, pts, cx, cy):
    return True, cx, cy


def fake_match_fails(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    return False, pred_cx, pred_cy, 0.0


capture()
t.flow_predict = fake_flow_only
t.template_match_locked = fake_match_fails
_states_d = []
_controllable_d = []
N_SOFT = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES
for i in range(N_SOFT + 2):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _states_d.append(t.track_state)
    with t.state_lock:
        _controllable_d.append(t.target_controllable)
print("    controllable по кадрам:", _controllable_d)
assert all(s == t.TRACK_STATE_TRACKED for s in _states_d), (
    "track_state покинул TRACKED раньше времени: %s" % _states_d)
assert all(_controllable_d[:N_SOFT - 1]), (
    "controllable упал раньше N_SOFT=%d кадров: %s" % (N_SOFT, _controllable_d))
assert not any(_controllable_d[N_SOFT - 1:]), (
    "controllable НЕ упал по достижении soft-порога N_SOFT=%d: %s"
    % (N_SOFT, _controllable_d))
print("    controllable=False начиная с кадра %d (single-signal gap "
      "достиг мягкого порога %d), track_state весь прогон остаётся "
      "TRACKED — persistent-порог %d кадров даже близко не достигнут"
      % (N_SOFT, N_SOFT, t.IDENTITY_DUAL_SIGNAL_GAP_MAX_FRAMES))

print("\n=== E. Dual-signal gap: один честный dual-signal кадр "
      "восстанавливает controllable немедленно (self-healing) ===")
t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
_clk.tick(FRAME_DT)
t.process_locked_tracker(scene)
assert t._match_dbg.get("identity_dual_signal_gap_frames") == 0
with t.state_lock:
    _controllable_e = t.target_controllable
assert _controllable_e, (
    "controllable не восстановился после честного dual-signal кадра")
print("    один dual-signal кадр -> gap_frames=0, controllable=True "
      "восстановлен сам")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t._shadow_match_against_template = _real_shadow_match
t.IDENTITY_ANCHOR_CHECK_ENABLED = t._orig_anchor_check_d
t.reset_tracking(to_acq=False)

print("\nOK: soft distrust снимает controllable НЕМЕДЛЕННО на первом "
      "измеренном признаке (anchor mismatch или начало long single-signal "
      "разрыва) — не дожидаясь, пока полный debounce наберёт persistent "
      "IDENTITY_UNCERTAIN (1.5-2.5с). Self-healing без reset_tracking(), "
      "как только anchor/dual-signal снова подтвердились.")
