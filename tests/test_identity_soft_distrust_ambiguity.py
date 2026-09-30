"""ARBITER-GATE на ambiguity/flow_gap soft distrust: если immutable anchor
недавно подтвердил позицию — sustained ambiguity/flow_gap НЕ должны
бесконечно душить controllable, только гейтить adaptation и снижать вес
матча (найдено оператором на реальных стендовых логах 2f7d275).

СЦЕНАРИЙ ИЗ ЛОГОВ. Захват №3: track_state=TRACKED весь заход, ambiguity
в ≈79.9% кадров (маленькая цель, matcher видит несколько почти равных
correlation peaks — норма для tiny target), anchor подтверждает позицию,
persistent UNCERTAIN НЕ триггерится (arbiter). Но override активен всего
≈25.8% кадров — soft_distrust снимал controllable по накоплению streak.

ТРЕБОВАНИЕ ОПЕРАТОРА: sustained ambiguity + fresh anchor confirmation
= streak растёт (диагностика остаётся), template adaptation блокируется
(через уже существующий _identity_ambiguous в _template_adaptation_gate),
но controllable оставаться True — arbiter говорит "это всё ещё та же
цель". Только если anchor arbiter НЕ подтвердил недавно — тогда те же
streak-условия действительно снимают controllable.
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
    t._match_dbg["second"] = 0.90
    return True, pred_cx, pred_cy, 0.90


def fake_match_clean(gray, pred_cx, pred_cy, flow_motion=0.0, tgt_dx=0.0, tgt_dy=0.0):
    t._match_dbg["second"] = 0.05
    return True, pred_cx, pred_cy, 0.90


N = t.IDENTITY_UNCERTAIN_CONFIRM_FRAMES

print("=== 1. Sustained ambiguity + real anchor confirms: track_state "
      "остаётся TRACKED и controllable ТОЖЕ остаётся True — arbiter "
      "признаёт, что это всё ещё та же цель, только 'lookalike' peaks "
      "мешают live-matcher быть уверенным (реальный сценарий захвата №3 "
      "из логов 2f7d275) ===")
capture()
_anchor_ref = t._identity_anchor_gray
_anchor_bytes = t._identity_anchor_gray.copy()
t.flow_predict = fake_flow_still
t.template_match_locked = fake_match_ambiguous
_states = []
_controllable_seen = []
_soft_seen = []
_streak_seen = []
_adapt_reasons = []
for i in range(N + 5):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    _states.append(t.track_state)
    with t.state_lock:
        _controllable_seen.append(t.target_controllable)
    _soft_seen.append(t._match_dbg.get("identity_soft_distrust"))
    _streak_seen.append(t._match_dbg.get("identity_uncertain_streak"))
    _adapt_reasons.append(t._match_dbg.get("adapt_skip_reason"))
print("    track_state:", _states)
print("    controllable:", _controllable_seen)
print("    identity_soft_distrust:", _soft_seen)
print("    identity_uncertain_streak:", _streak_seen)

assert all(s == t.TRACK_STATE_TRACKED for s in _states), (
    "arbiter должен был блокировать persistent UNCERTAIN всё время — "
    "получили states=%s" % _states)
assert all(_controllable_seen), (
    "controllable ушёл в False хотя бы раз, хотя arbiter подтверждает "
    "позицию — sustained ambiguity сама по себе НЕ должна душить "
    "override, если anchor согласен: %s" % _controllable_seen)
assert not any(_soft_seen), (
    "identity_soft_distrust включался хотя бы раз, хотя arbiter recently "
    "confirmed — arbiter-gate не сработал: %s" % _soft_seen)
assert _streak_seen[-1] >= N, (
    "identity_uncertain_streak не рос — тест сам по себе негоден, "
    "ambiguity должна была накапливать счётчик")
_ambig_reasons = [r for r in _adapt_reasons if r == "ambiguous_peak"]
assert len(_ambig_reasons) >= 1, (
    "template_adaptation_gate не блокировал ambiguity даже одного раза — "
    "arbiter-gate не должен был отменять уже существующие защиты по "
    "ambiguity, только soft distrust: %s" % _adapt_reasons)
assert t._identity_anchor_gray is _anchor_ref
assert np.array_equal(t._identity_anchor_gray, _anchor_bytes)
print("    OK: track_state=TRACKED всё время, controllable=True (arbiter "
      "гарантирует), streak растёт как диагностика (%d к концу), "
      "template adaptation по-прежнему блокируется на ambiguity (reason="
      "'ambiguous_peak' %d кадров)" % (_streak_seen[-1], len(_ambig_reasons)))

print("\n=== 2. Контрольная секция: ambiguity + anchor arbiter отключён "
      "-> soft distrust срабатывает как раньше, доказывая, что защита "
      "именно arbiter-gate'ом, а не выключением всей ambiguity-ветки "
      "soft distrust ===")
t.reset_tracking(to_acq=False)
capture()
_orig_arbiter = t.IDENTITY_ANCHOR_ARBITER_ENABLED
t.IDENTITY_ANCHOR_ARBITER_ENABLED = False
t.flow_predict = fake_flow_still
t.template_match_locked = fake_match_ambiguous
_controllable_arbiter_off = []
for i in range(N + 2):
    _clk.tick(FRAME_DT)
    t.process_locked_tracker(scene)
    with t.state_lock:
        _controllable_arbiter_off.append(t.target_controllable)
t.IDENTITY_ANCHOR_ARBITER_ENABLED = _orig_arbiter
print("    controllable при arbiter OFF:", _controllable_arbiter_off)
assert not _controllable_arbiter_off[-1], (
    "с отключённым arbiter'ом sustained ambiguity должна была снять "
    "controllable через дебаунс — не сработало, значит защита была не "
    "arbiter-gate'ом, а чем-то другим: %s" % _controllable_arbiter_off)
print("    OK: с arbiter OFF sustained ambiguity корректно триггерит soft "
      "distrust — механизм именно arbiter-зависимый")

t.flow_predict = _real_flow_predict
t.template_match_locked = _real_template_match_locked
t.reset_tracking(to_acq=False)

print("\nOK: sustained ambiguity + свежее anchor-подтверждение НЕ "
      "снимает override (реальный сценарий захвата №3 из логов 2f7d275) "
      "— arbiter решает, что это всё ещё та же цель. Ambiguity-ветка "
      "продолжает работать как диагностика (streak, event log) и "
      "продолжает блокировать template adaptation, но override живёт. "
      "Anchor-check-streak и dual-signal-gap как источники soft distrust "
      "arbiter-gate'ом не задеваются — те гейтятся другими механизмами.")
